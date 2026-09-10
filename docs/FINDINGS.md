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

## 10. Assorted

- **CEF/HTML5 keyer on CPU tops out ~50 fps at 1080p** (SwiftShader, no GPU on
  Azure D-series): keying 1080p60 drifts and eventually freezes. Run the chain
  at 1080p30 (fine for graphics) or use a GPU instance.
- **hls2mxl silently stops at VOD EOS** (status still `running`) and stalls on
  some live-HLS streams; live **RTSP** input works well.
- **Selector caps at 3 inputs** (hard limit in the Rust backend).
- **Any NAT/cloud WebRTC deployment needs** `MTX_WEBRTCADDITIONALHOSTS=<public-ip>`
  on MediaMTX, or viewers get only private ICE candidates (black video).
- **Prebuilt images assume AVX.** On a QEMU VM with the default virtual CPU
  model (x86-64-v2, no AVX), every libmxl call SIGILLs (`vxorps`). Set the
  hypervisor CPU model to `host`. A runtime CPU-feature check with a clear
  message would make demo-day failures comprehensible.
