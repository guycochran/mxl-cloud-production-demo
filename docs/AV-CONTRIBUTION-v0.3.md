# A/V Contribution — v0.3 scope

**Status: SCOPE / design, not built.** This proposes how to generalize the
contribution seam from video-only to video **+ audio**, so a contributor
(guest, camera, ZoomISO Cloud) is ingested as a *participant* — one front end,
both essences — instead of two hand-maintained, divergent scripts.

Read [`CONTRIBUTION-SEAM.md`](CONTRIBUTION-SEAM.md) first; this extends it.

---

## 1. Why this is the v0.3 headline

v0.2 made the lab adoptable. v0.3 makes it able to run a **real show**, and a
real show has audio. Today the audio path is where the video path was before the
seam refactor: copy-pasted, drifting, and wired to the live facility.

- `tools/guest_audio.py` is a near-exact **audio twin** of the old
  `guest_ingest.py` — rtspsrc → decode → conform → restamp → mxlsink — but it
  predates `ContributionCore` and shares none of it.
- It hardcodes `rtsp://10.0.0.5:8554` (the `mxl_vm_internal` VNet address now in
  `config/facility.json`) and assumes the prodbots topology.
- `tools/audio_pgm.py` is the program **mixer**, not an ingest — but it carries
  its own restamp/offset discipline and its flow map now comes from the manifest
  (done in the facility.json sweep). It is in scope only at its *edges* (where it
  reads contributed audio flows), not for a rewrite.

The seam already proved the pattern works for N video transports. Audio is the
same five stages with a different conform and a different restamp — exactly the
kind of variation the `SourceAdapter` / `ContributionCore` split exists to hold.

---

## 2. The shape of the problem

A video ingest and an audio ingest are the **same back half** with two
differences:

| Stage | Video (shipped) | Audio (today, in guest_audio.py) |
|---|---|---|
| front end | rtspsrc/srt → h264 decode → queue | rtspsrc → decodebin → queue |
| conform | `videorate ! videoscale ! videoconvert ! v210 caps` | `audioconvert ! audioresample ! F32LE/48k/2ch caps` |
| restamp | offset-map PTS onto local clock +2 grains, RESYNC on sustained drift | accumulate PTS by buffer duration, reset on >500ms gap |
| sink | `mxlsink … flow-id=…` (video-flow) | `mxlsink … flow-id=…` (audio-flow) |
| announce | `/api/mxl/repair` on lock (opt-in) | none (mixer tolerates absent flows) |

So the generalization is **not** "add audio to the video core" — it's "recognize
the core is media-essence-agnostic except for conform + restamp, and
parameterize those two."

---

## 3. Proposed design

### 3a. Essence as a property of the adapter, conform/restamp chosen by the core

Add an **`essence`** dimension (`'video' | 'audio'`) alongside the existing
orthogonal `needs_conform` / `timing_policy`. The core already branches on
`needs_conform` to pick the conform stage; it gains a parallel branch on
`essence` to pick:

- the **conform caps** (`CANON_CAPS` for video; a new `CANON_AUDIO_CAPS` =
  `F32LE,48000,2ch` for audio),
- the **conform elements** (`videorate!videoscale!videoconvert` vs
  `audioconvert!audioresample`),
- the **restamp probe** (the existing offset-map for video; the
  duration-accumulate variant for audio — both are small, both already exist).

`SourceAdapter` stays the contract. A new `AudioSourceAdapter` base (or an
`essence='audio'` class attribute) carries the audio defaults. `source_fragment()`
is unchanged in spirit — it still returns the front-end fragment ending at a
queue; the core appends the essence-appropriate conform + sink.

**Why not two cores:** the restamp/announce/bus-handling/supervisor-exit logic is
identical and hard-won. Forking it re-creates the divergence we're removing. One
core, two conform/restamp tables.

### 3b. A participant = a pair of adapters

A guest contributes **video + audio together**. Model that as a thin
`Participant` that owns two adapters (one per essence) sharing identity (label,
slot group) and front-end address, each running in its own
`ContributionCore` process (preserving the one-process-per-flow supervisor model
the demo relies on — do **not** multiplex two essences into one pipeline; a
wedged audio decode must not take video down).

```
Participant "Guest 1"
  ├─ VideoAdapter(src=srt/rtsp guest1)  → ContributionCore(essence=video) → Guest 1 flow
  └─ AudioAdapter(src=same guest1)      → ContributionCore(essence=audio) → Guest 1 Audio flow
```

The pair shares a manifest-driven identity: `video_flows.guest1` +
`audio_flows.guest1`, labels "Guest 1" / "Guest 1 Audio", already both in
`config/facility.json`.

### 3c. De-prodbots the audio front end

`guest_audio.py`'s `rtsp://10.0.0.5:8554` becomes:
`rtsp://{network.mxl_vm_internal}:8554/{path}` read from the manifest, with the
same literal fallback pattern used across the facility.json sweep. The announce
target stays the opt-in `MXL_REPAIR_URL` boundary the video core already honors —
audio ingests default to **no announce** (the mixer already tolerates absent
flows), so an adopter's audio ingest never phones anyone.

---

## 4. Phased plan (each phase independently shippable + verifiable offline)

1. **✅ DONE — Audio conform/restamp into the core.** Added `essence` to
   `SourceAdapter`, `CANON_AUDIO_CAPS`, the audio conform branch in
   `_build_launch`, and `_restamp_audio` (duration-accumulate) to
   `ContributionCore`; `run()` picks the probe by essence. Added
   `AudioGuestAdapter`. `tests/test_launch_parity.py::test_guest_audio_launch_string_matches_shipped_guest_audio`
   pins the generated pipeline BYTE-IDENTICAL to `guest_audio.py`'s. All via
   stubbed-gi, no hardware. `guest_audio.py` itself is untouched (it stays the
   reference until Phase 2 re-expresses it as adapter + core.run()).
2. **✅ DONE — `AudioGuestAdapter` + guest_audio.py as a thin shim.** Re-expressed
   `guest_audio.py` as `AudioGuestAdapter + ContributionCore.run()`, exactly like
   `cam_ingest.py`. Manifest-driven flow (arg > manifest `audio.guest1` > fallback);
   host via `MXL_AUDIO_RTSP_HOST` (default `10.0.0.5`). CLI contract preserved
   (bring-up's `guest_audio.py <path> <flow> <label>` still works; import-copy order
   in bring-up already correct — core/adapters/facility land before guest_audio).
   `test_guest_audio_is_a_thin_shim` guards against re-inlining a pipeline. Byte-
   identical to the old pipeline (parity test). (`AudioRtspAdapter` for cams with
   embedded audio = deferred, no current need.)
3. **✅ DONE (quickstart) — `Participant` helper + quickstart A/V wiring.**
   `tools/participant.py`: a DESCRIPTOR/BUILDER (not a runner — ContributionCore.run()
   blocks, and the two essences must stay in separate processes). `Participant.guest(n)`
   resolves video+audio adapters from the manifest; `.legs()` emits the two per-essence
   launch commands, argv byte-compatible with the existing guest_ingest.py/guest_audio.py
   CLI so a supervisor adopts it with no behaviour change. Adapters imported LAZILY so
   the data path works with no GStreamer. **quickstart.sh now launches an audio leg per
   guest** (`run_guest_audio`, `host.docker.internal` source for the single box,
   `MXL_GUEST_AUDIO=0` to skip) — closing the adopter-path gap where guests were
   video-only. `docker/guest-ingest.Dockerfile` now ships `guest_audio.py` + `facility.py`
   + `config/facility.json`. `tests/test_participant.py` pins flows + CLI contract.
   *(bring-up-mxl.sh left as-is — it already launches both legs correctly via its
   guest-audio loop; converting it to Participant is a safe later cleanup, gated behind
   a byte-identical check before it touches the OHG box.)*
4. **ZoomISO audio.** The `ZoomIsoMxlAdapter` stub becomes a *pair* — a Zoom
   participant has audio too. Folds into the "measure at beta" open questions
   below; no build until the beta lands.

---

## 5. Open questions (answer before/at build, not now)

1. **Audio restamp vs video restamp drift interaction.** Video and audio are
   restamped independently, each in its own process. **Decision: keep them
   independent; measure, don't pre-couple.** Reasoning: both probes reference the
   SAME base — `pipeline.get_clock().get_time() - base_time` — with the SAME
   `MARGIN_NS`. Because each re-reads `now` every frame and maps onto it, neither
   accumulates error against the other; this is a shared *time base*, not two free
   clocks. So the risk is **not growing drift** — it's a **fixed initial-offset
   skew**: video locks its offset on its first frame, audio on its first frame, and
   those arrive at slightly different wall-clock moments (different decode latency).
   That skew is bounded (tens of ms, within typical lip-sync tolerance), constant,
   and measurable. Forcing a shared clock reference now would add cross-process
   coupling (shared-memory/file handshake) that the one-process-per-flow model
   deliberately avoids. **Fallback if a real guest shows audible skew:** a tiny
   shared `offset-lock` handshake (first core to lock writes `now+MARGIN` to a
   per-participant tmpfile; the other adopts it instead of locking its own) — a
   localized change, not a redesign. Measure on a real guest before building the
   fallback.
2. **One mixer input per guest, or per guest *essence-pair*?** `audio_pgm.py`
   reads contributed audio flows by name; confirm its SOURCES map stays correct
   when audio ingests are produced by the new adapter (it should — same flow
   UUIDs, manifest-driven).
3. **Cameras with embedded audio.** Studio cams are video-only today; a Makito
   with embedded SDI audio would want the pair too. Out of scope for v0.3 unless
   a real need appears — note it, don't build it.
4. **ZoomISO audio flow shape** — same "N flows vs composite, aligned vs foreign
   clock" questions as video (CONTRIBUTION-SEAM §4), now doubled for audio.

---

## 6. Explicitly out of scope for v0.3

- Rewriting `audio_pgm.py` (the mixer). It's manifest-wired already; v0.3 only
  touches where it *reads* contributed flows.
- Any ZoomISO build (waits for the beta).
- A generic local control plane / de-prodbots backend — that's a separate P1.

---

## 7. Success criteria

- A guest contributes video **and** audio through one `Participant`, both legs
  running on `ContributionCore`, with the audio pipeline byte-identical to
  today's `guest_audio.py`.
- No audio UUID, host, or announce target is hardcoded — all manifest-driven,
  all opt-in for external calls.
- Lip-sync holds across a reconnect on a real guest (open question 1, measured).
- An adopter can ingest a guest's A/V on a quickstart box with no prodbots
  dependency.
