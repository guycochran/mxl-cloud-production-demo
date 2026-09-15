# Field Findings — running MXL as a real remote production

Notes from building this demo (September 2026, MXL SDK v1.1 era, stock
`ghcr.io/cbcrc` images on an Azure D8s_v5). Offered as user feedback to the
MXL / mxl-hands-on / easy-mxl projects. Everything below was measured, not guessed.

## 1. Remote sources stutter when cut — and why (timing model)

MXL grains are indexed by **TAI timestamp**, not queue order (`docs/Timing.md`).
A remote camera's grains arrive ~transport-latency late, so a naive gateway writes
them at indices behind the locally-generated flows' heads. A reader that aligns
multiple flows (the input selector reads near "now") then finds the remote flow's
grain **not yet written** → `TOO EARLY` / EAGAIN → the slot stutters, while
single-flow reads of the same camera are flawless.

**Fix that worked:** re-publish the remote flow with its PTS shifted so its head
aligns with the local flows (a "latency normalizer"). Two generations:

- `cam_relay.py` — fixed +offset on an existing gateway flow. Works, but the
  offset hard-codes the transport delay.
- `cam_ingest.py` — own RTSP ingest, offset **locked from the first buffer**
  (self-tuning) with slow drift re-sync. This is the keeper.

Upstream suggestion: the selector could read at `min(head)` across its inputs
(per Timing.md's own multi-flow alignment note), or accept a per-slot read offset.

## 2. Don't stamp arrival time into grain indices (pan stutter)

First version of `cam_ingest.py` stamped each buffer at **arrival time**. Under
motion (camera pans), encoder bitrate spikes and network jitter grows; arrival
jitter quantized directly into grain indices produces doubled/skipped grains —
visible stutter exactly when the camera moves. **Keep the stream's own regular
PTS cadence and add one constant offset.** Verified with a scripted VISCA pan +
per-frame YDIF analysis: zero duplicate frames in motion after the fix.

## 3. "Robustness when grains are missing" — measured (white paper open topic)

Flows are small rings (6 grains for 1080p30 video here). If a writer skips an
index (or a reader races a write), the reader gets **whatever is still in that
ring slot** — with a switcher upstream that can be a frame of the *previous
source*. We recorded the program and caught two single-frame luma spikes
(YAVG 38.8 → 58.6 → 38.8): one frame of test pattern flashing inside camera
program. **Mitigation:** write the normalized flow ~2 grains ahead of "now" so
aligned readers never race the writer. After the margin: 90 s / 2,685 frames,
zero spikes. A reader-side "index was never committed" signal would let apps
freeze-last-frame instead of showing stale content.

## 4. The 2-second jitterbuffer hiding in `uridecodebin`

The stock HLS/RTSP gateway builds `uridecodebin` with defaults; for RTSP inputs
that means `rtspsrc latency=2000` — a silent 2 s of latency. Setting it in
`source-setup` (150–200 ms works fine over SRT + LAN-quality links) removed
almost all of our glass-to-glass latency. Result: camera OSD clock and the
cloud-keyed clock read the **same second** in a single program frame.

## 5. `mxlsink` needs an explicit `flow-id`

An empty `flow-id` fails deep in negotiation as `not-negotiated (-4)` with the
real error (`Flow ID is invalid: invalid length: expected length 32`) only
visible at `GST_DEBUG=mxlsink:5`. Cost us a night of blaming caps. A fail-fast
property check with a clear message would save others the same.

## 6. Readers wedge when a source flow is recreated

Restarting any writer recreates its flow file; downstream readers (selector,
keyer, encoder) keep stale handles and stall silently — status APIs still say
`running`, no error surfaced. Recovery is a **downstream cascade restart** in
dependency order (see `backend/mxl-routes.js` `/api/mxl/repair`). Upstream
suggestions: readers could detect flow-file replacement and re-open; status
APIs should report a stalled read.

## 7. Cross-host fabric: it works, and the receiving host is idle

We bridged the domain across VMs with the Fabrics API (TCP provider) and ran it
as a three-node cluster (production VM → fabric peer VM → isolated "island" VM):

- **30 grains/s sustained** shipping the live 1080p30 v210 program (~1.3 Gbps),
  zero drops over 100k+ grains per leg, bidirectional legs concurrently.
- **Initiator ~10% of one core; target ~0% CPU** — remote writes land in the
  destination ring without the receiving host's CPU in the data path.
- Destination write-age 0.2–8 ms; with both hosts on NTP, the remote flow's
  head index equalled the locally-generated flows' heads — the stock input
  selector cut the fabric-delivered flow on air with no special handling.
  TAI-indexed grains quietly solve multi-flow alignment when hosts are synced.
- **libfabric ≥ 2.x is required** (`FI_SOCKADDR_IP`): Ubuntu 24.04's packaged
  1.17 — and even 1.22 — fail; we built 2.6.0 from the release tarball.
- **Non-routed networks:** TargetInfo embeds the target's *private* bind
  sockaddr. Patching the address bytes ([4:8] of the base64 blob) to the
  target's public IP lets the initiator connect across unpeered VNets through
  cloud NAT at full rate — which is the exact recipe for cross-region or
  cross-cloud fabric. (Our actual cross-region attempt was blocked only by
  subscription SKU capacity, not by the technology.)
- Upstream suggestion: a supported "advertised address" field in TargetInfo
  would make NAT traversal first-class instead of a byte-patch.

## 8. A broadcast contribution encoder as an MXL source (Makito X4)

A second, static camera joins the selector without any studio-side re-encode
hop: SDI camera → Haivision Makito X4 (deinterlacing 1080i29.97 → 1080p30 in
the encoder, HEVC Main10, 20 Mbps) → SRT caller straight to the cloud VM's
MediaMTX (`streamid=publish:cam2`) → GStreamer ingest → v210 flow → selector
slot 3. Three lessons:

- **gst-libav defaults to slice threading in live pipelines.** With a
  single-slice stream that means one core, and HEVC 1080p30 10-bit missed
  realtime by ~30% on our loaded box — stream PTS fell behind wall clock and
  the cadence lock re-synced (~1 s grain jump) every few seconds. The fix is
  `avdec_h265 thread-type=frame max-threads=4` (a few frames of added latency,
  irrelevant for a static shot). Measured after: 30.00 fps, mapping error
  under 60 ms over 10+ minutes.
- **Don't "fix" decoder threading encoder-side with multi-slice.** Setting the
  Makito to 4 slices broke MediaMTX's H.265-in-TS framing ("PTS is missing"
  decode errors, only the first slice of each AU survived → top quarter of the
  picture over solid green). Keep `slices=1` when MediaMTX is the SRT server.
- **29.97 vs the domain's exact 30/1 grain rate** is real: a `videorate`
  element re-times the stream (one duplicated frame every ~33 s) or caps
  negotiation fails outright against a 30/1 flow.

Also considered: HEVC 4:2:2 10-bit is a bit-perfect match for v210 (the
Makito encodes it natively), but its software-decode cost (~1.3 cores) wasn't
worth it on a saturated 8-core VM when the WebRTC leg is 4:2:0 8-bit anyway —
Main10 4:2:0 at 20 Mbps is transparent for a locked shot.


## 9. The real cost of software media on a shared VM (and how we shed it live)

Running the whole chain — CEF keyer, x264 WebRTC encoder, two camera decodes,
plus a fabric leg — on one 8-vCPU VM ran it at ~13/8 load. Under visitor load
the CEF keyer (software SwiftShader render, no GPU) lost its cores and its
output flow went stale, freezing the public program. Two things fixed it
without a bigger box:

- **HEVC → H.264 for the contribution camera.** Software HEVC decode of the
  Makito's 1080p30 feed cost ~1.3 cores; the same stream as H.264 High costs
  ~0.5. For a locked static shot at 12 Mbps the quality difference is invisible
  in the 4:2:0-8-bit WebRTC output. When a software decoder is on the critical
  path, codec choice is a scheduling decision, not just a bitrate one.
- **Each `mxlsrc` SHM reader busy-spins a full core.** The audio-follow-video
  writer held two of them (episode + tone) and alone accounted for ~2 cores of
  the overload. Since the studio is silent, we dropped audio entirely and run
  the encoder video-only. Lesson: a polling SHM reader is not free even when
  the media it carries is silence — account for readers, not just writers.

Result: load fell from ~13/8 to ~4/8; the freezes stopped.

## 10. An MXL-native multiviewer: compose in the domain, encode once

A 3×3 production multiview built the way a hardware switcher does it — as a
flow, not as nine video players. One CPU compositor reads all seven switcher
inputs plus the keyed program straight out of shared memory and writes the
wall back to the domain as a new v210 flow; a single x264 leg encodes that
one flow for every browser viewer. No GPU, and no source is ever decoded
(the tiles are scaled from the same v210 grains the selector cuts).

```
 CONTRIBUTIONS                       MXL SHARED-MEMORY DOMAIN
                                    ┌──────────────────────────────────────────────┐
 Cam 1 (SRT) ──► ingest ──────────► │ Cam 1     ─┐                                 │
 Cam 2 (SRT) ──► ingest ──────────► │ Cam 2      │                                 │
 Episode file ─► file-player ─────► │ Playout    │  8× mxlsrc                      │
 Guest 1 ─(fabric from host 2)────► │ Guest 1    ├────► compositor (CPU)           │
 Guest 2 ─(fabric from host 2)────► │ Guest 2    │      3×3 · 640×360 tiles        │
 Test generator ──────────────────► │ Pattern    │      1080p v210                 │
 layout compositor ───────────────► │ Layout     │         │ mxlsink               │
 HTML5 keyer (program+gfx) ───────► │ Keyer PGM ─┘         ▼                       │
                                    │            "Multiview PGM" flow — a peer     │
                                    │            flow: listable, probeable,        │
                                    │            even cuttable to program          │
                                    └──────────────────────┬───────────────────────┘
                                                           │ mxlsrc
                                                           ▼
                                       ONE encoder for all 8 tiles:
                                       x264 ── MPEG-TS ── SRT ── MediaMTX ── WebRTC
                                       (tally borders + click-to-arm are HTML
                                        overlays on the fixed grid in the web UI)
```

Cost: ~2 cores of a D32s_v5 for the whole wall at 15 fps, ~3.5 at 30 fps
(measured: doubling the cadence cost ~1.7 cores — the per-branch 1080p
videoconvert dominates, and it scales linearly with output fps). We ran it
at 30 fps for a day and went back to 15: with the full production chain
beside it the box sat at load 14/32 and the *program* encoder started
stuttering under thread contention during a live demo. On 640×360 tiles the
cadence difference is barely visible; the headroom is worth far more than
the frames. A multiview is the first thing to de-rate when the program is
the product. Note the recursion:
compositor input #7 is *itself* a composited flow (the 2-up/PiP/4-up layout
engine) — domain flows compose like any other source.

Lessons:

- **`compositor ignore-inactive-pads=true` EOS's instantly at startup** — every
  pad is "inactive" before first data, so the aggregator declares the stream
  over (a silent exit, easily misread as a crash). A `latency=` timeout on the
  compositor alone handles absent sources correctly.
- **A live `mpegtsmux` in application code needs pipeline latency handling**
  that `gst-launch` does for free: listen for the `latency` bus message and
  call `recalculate_latency()`, and keep an `identity` between the mux and
  `srtsink` — without it the mux's aggregation never opens (encoded frames in,
  zero TS out, no error anywhere).
- **MediaMTX silently discards SRT payloads that aren't 1316-byte aligned.**
  `mpegtsmux` defaults to one 188-byte TS packet per buffer; the publisher
  shows "publishing", readers get zero bytes, nothing logs. `alignment=7` is
  mandatory. ffmpeg-based publishers never hit this (1316 B is its default),
  which is exactly why it was hard to spot.

## 11. Assorted

- **CEF/HTML5 keyer on CPU tops out ~50 fps at 1080p** (SwiftShader, no GPU on
  Azure D-series): keying 1080p60 drifts and eventually freezes. Run the chain
  at 1080p30 (fine for graphics) or use a GPU instance.
- **hls2mxl silently stops at VOD EOS** (status still `running`) and stalls on
  some live-HLS streams; live **RTSP** input works well.
- **Selector defaults to 3 inputs** — it looked like a hard limit, but it's the
  container's `MAX_INPUTS` env (we run 7: recreate the input-selector container
  with `-e MAX_INPUTS=7`; a from-scratch recreation without it silently rejects
  slots ≥ 3).
- **Any NAT/cloud WebRTC deployment needs** `MTX_WEBRTCADDITIONALHOSTS=<public-ip>`
  on MediaMTX, or viewers get only private ICE candidates (black video).
- **Prebuilt images assume AVX.** On a QEMU VM with the default virtual CPU
  model (x86-64-v2, no AVX), every libmxl call SIGILLs (`vxorps`). Set the
  hypervisor CPU model to `host`. A runtime CPU-feature check with a clear
  message would make demo-day failures comprehensible.

## 12. The reader-lifecycle bug is a family — the full taxonomy

§6 established that a reader wedges when its source flow is recreated. In a
week of production it revealed **five distinct species**, each needing a
different detector. This is the single most important thing we learned and
the strongest upstream candidate.

| Species | What you see | What rate/head/thumb checks say | Detector that actually works |
|---|---|---|---|
| **Recreation wedge** | reader silent forever after a writer restart | "attached", head advances on the *writer* side | per-reader delivery timestamp (no buffers in >N s) |
| **Repeat-delivery wedge** | full 30 fps of the *same* grain | all green — grains tick, head fresh, thumbs "update" | unique-content count (md5 a slice) / byte-identical decoded frames |
| **Frozen-slices** (+ stealth variant) | fabric target grains tick but slice payload stalls; log may stay live | grains/s = 30 | `avg slices/grain` decaying below 1080 |
| **Stale-attach lag** | a *fresh* reader on a healthy flow delivers late/nothing for seconds–minutes ("first cut to that source is a dud") | flow is fine; only the new reader is behind | first-activation content check within ~2.5 s |
| **Fresh-attach-dead flow** | a flow enters a state where *every* new reader gets nothing despite a live writer | flow head fresh, writer healthy | N consecutive failed fresh attaches → **bounce the writer**, not the reader |

**The load-bearing insight:** rate, head-freshness, and thumbnail-updating
checks *all read green through several of these species.* The only honest
detectors are **content-uniqueness** at the domain and **byte-identical
decoded-frame counting** at the encoded output (see §14).

## 13. The architectural fix: a flow stabilizer (indirection)

Every mitigation in §12 is a bandage. The real fix is `flow_stabilizer.py`:
a per-input relay that reads the **volatile** source flow and writes a
**stable** flow that is created once and never recreated. When the volatile
side churns, it swaps only its own reader in place — the sink and the stable
flow never stop, so every downstream consumer (selector, layout panes,
thumbs, probe) is permanently insulated from recreation.

Design lessons paid for the hard way:
- **Separate the output clock from the input.** v1 passed input timestamps
  through; a stalled or bursty source made the stable flow's timeline jump.
  v-final drives output from a **fixed 30 fps grid slewed to the wall clock**
  and *drops* ahead-of-realtime arrivals — the output timeline is monotonic
  and real-time *by construction*, which structurally kills the re-lock class.
- **The stabilizer process must be immortal.** A supervisor respawn would
  recreate the stable flow — the exact failure it exists to prevent. Any
  GStreamer error/EOS swaps the reader instead of exiting (with a bounded
  "N dry swaps → last-resort exit" backstop).
- **Detect recreation by directory inode change**, not by silence — an idle
  guest is silent legitimately; a recreated flow has a new inode.
- **Proven in isolation** (a single persistent reader rode a full volatile
  kill+recreate) but **not battle-tested under a live show** — we rolled it
  back after iterating on it too aggressively mid-demo. It is the right
  post-v1.1.0 topology; deploy it from day one on a fresh build rather than
  retrofitting under fire.

## 14. Measurement doctrine: rate metrics lie

If you take one operational lesson: **"is it flowing" is not "is it good."**

- **Grain rate, head freshness, thumbnail-updates** all stay green through a
  repeat-delivery wedge. They measure *motion of the plumbing*, not *change
  of the content*.
- **Content uniqueness** (hash a fixed slice of each grain; count distinct
  per second) catches repeat-wedges at the domain. `grain_probe.py` does this
  across every flow.
- **Byte-identical decoded frames** (`ffmpeg signalstats YDIF==0`) is the only
  honest program-integrity signal: x264 emits skip-blocks for truly repeated
  input, so a wedged encoder decodes byte-identical, while real video —
  always carrying sensor noise — never does. **Similarity metrics
  (mpdecimate) FALSE-FIRE on static scenes** (a locked-off night shot) — we
  shipped that mistake, it needlessly restarted a healthy encoder, and we
  caught it in its own log. YDIF has no such failure mode.
- **Hash the CENTRE of the frame, not the top.** Letterboxed 2-ups and keyed
  frames have static top rows; the first 4 KB of a 1080p v210 buffer is under
  one scanline. Off-centre hashing reports false "frozen".

## 15. Self-heal reflexes need a single arbiter

We accreted watchdogs under fire — repair cascade, guest-leg doctor,
layout wedge-watch, take-check, fps-doctor, selector-doctor, startup repair.
Individually each is sound. **Together, uncoordinated, they amplify.**

- A stale guest branch tripped the layout take-check → engine respawn →
  startup repair cascade → a cascade-embedded warm-up that flashed **all six
  inputs on air** → the churn re-tripped the babysitter → repeat. 38 repair
  events in 30 minutes, visible on program.
- **Fixes that worked:** make the warm-up **manual-only** (never ride a
  cascade); make take-check **log-only** (a stale pane is honest; a respawn
  storm is not); cap each reflex with **per-branch strike files + cooldowns**;
  and — the rule — **no cascade on a timer**, only on a deliberate trigger
  (human, a doctor healing a *confirmed* fault, or a respawn *this instant*).
- **The design lesson:** past a handful of self-heal reflexes, they need one
  supervisor with a **global action budget**, not N independent loops. A
  cascade is a dice roll (it rebuilds readers that were fine); fire it only
  when something *knows* the chain is broken.
- **A warm-up ("line-up") sweep is legitimate** — activating every selector
  slot briefly so no reader is cold on first cut, exactly like a TD walking
  the rail before air. Just keep it off the automatic paths.

## 16. Operational recipes (the runbook)

- **mediamtx wedges silently** — accepts SRT publishes but stops serving
  readers. Recipe: `docker restart mediamtx` + re-publish. Killed a camera
  once; not obvious from any status.
- **A "slow but flowing" fabric leg** (e.g. 4.3 grains/s while the writer
  sends a clean 30) trips *no* doctor — no missed-grain spam, no frozen
  slices, head stays fresh. Only a rate probe catches it; bounce the leg.
- **Thumbnail readers wedge on guest reset** like any other reader. Heal them
  as aggressively as program (stall 6 s, frozen-content 10 s, inode-recreate
  check) — a frozen *preview* makes an operator distrust a *live* source and
  not cut to it. Lower stakes, but it breaks confidence.
- **Cloudflare 100s edge timeout** can 502 a long repair mid-flight — drive
  repairs against the local backend (`127.0.0.1:3013`), not the public URL.
- **Cloudflare blocks the python-urllib UA** (CF-1010) — any in-domain tool
  polling the public API needs a browser-like `User-Agent`, or use localhost.
- **The status API's 500 ms micro-cache lies right after a cut** — a client
  that adopts the cached snapshot immediately after acting scrambles its own
  bus state. Hold local state ~1.2 s after an action before trusting the poll.

## 17. Standards & control plane (what we proved)

- **NMOS discovery works today.** `tools/nmos_node.py` is a stdlib IS-04 v1.3
  Node presenting every MXL flow as a standard Sender/Receiver — transport
  `urn:x-nmos:transport:mxl`, `mxl_domain_id`/`mxl_flow_id` tags per **AMWA
  BCP-007-03** (published v1.0), live PGM/PVW tally as grouphint tags.
  Registered into an nmos-cpp registry, **Bitfocus Buttons discovered the
  cloud facility** — first MXL facility in an NMOS registry that we know of.
- **A hardware panel is one Companion module away.** `companion-module-mxl-switcher/`
  drives a Stream Deck XL with real red/green tally over the switcher's REST.
  Confirmed on hardware (Companion 5.0.5). Buttons 1.7 won't sideload custom
  modules (signed-only) and its import wants a ZIP backup, not a
  `.companionconfig` — so panel = classic Companion, standards story = Buttons.
- **The gap to full facility:** we built IS-04 (discovery). The next rung is an
  **IS-05 connection shim** so a controller can *route* us, then IS-07 tally
  and IS-08 audio. Each is independently demoable without touching the data
  plane. That sequence turns "a demo" into "a facility."
