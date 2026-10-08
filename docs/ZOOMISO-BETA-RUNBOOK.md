<!-- SPDX-License-Identifier: Apache-2.0 -->
# ZoomISO Cloud — first 30 minutes with the beta (measure → wire → cut)

**Goal:** when the ZoomISO Cloud beta lands, go from "we have access" to "Zoom participants
are cuttable slots on the switcher" in ~30 minutes — by *measuring* the few genuine unknowns,
not flailing. The ingest plumbing is already built and **proven on a synthetic native-MXL
source** (see "What's already proven" below); only the ZoomISO-specific facts need measuring.

## What's already proven (dry-run, no real product)
- `ZoomIsoMxlAdapter` reads a native MXL v210 flow via `mxlsrc video-flow-id=<uuid>`
  (needs_conform=False) and republishes it to a selector slot — verified: a synthetic source
  became a live "Zoom 1" flow and the selector **cut to it** (HTTP 200, stable 30fps).
- `timing_policy='align'` logs its honest "assumed, not verified" and skips restamp; flipping
  to `'restamp'` is a one-kwarg change.
- Both flow shapes are handled by `zoomiso_adapters(..., mode=...)` — see below.
- Harness: `tools/zoomiso_dryrun.py`. Hard-won gotcha already fixed: **mxlSRC uses
  `video-flow-id`, not the `flow-id` that mxlSINK uses.**

## The three things to MEASURE on beta day (do NOT assume)

### Q1 — one flow per participant, or one composite?
Bring a ZoomISO Cloud session up pointed at your MXL domain, then:
```
docker exec input-selector /opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l
```
- **N flows** (one per participant, likely labeled per name/seat) → `mode='per_participant'`
  → each participant becomes its own cuttable slot (the ideal — real multi-cam of a call).
- **1 flow** (the whole gallery/active-speaker as one picture) → `mode='composite'` → one slot;
  switch inside Zoom, or add a `layout_pgm` crop stage later to split it.

### Q2 — audio: separate flows, or muxed?
In the same `mxl-info -l`, look for Audio flows alongside the Video ones. ZoomISO may emit
`audio-flow-id` separately. If so, the adapter reads video; wire audio into `audio_pgm.py` the
same way guest audio is handled (`tools/guest_audio.py` is the pattern).

### Q3 — clock: aligned to our domain, or foreign?
With a ZoomISO flow live, run the adapter in **align** mode (default) and watch the diag line:
```
docker logs <zoomiso-ingest> | grep diag    # fps should be ~30.0, steady
```
- Steady ~30fps, cuts look clean, in-picture motion matches the program clock → **align is correct**,
  leave it.
- Judder, periodic re-syncs, or the selector refusing to cut it cleanly → the ZoomISO clock is
  foreign → **flip to `timing_policy='restamp'`** (the same cadence-restamp every SRT source uses).
  One kwarg; no other change.

### (Q4 if cross-host) — does it arrive via a fabrics proxy?
If ZoomISO Cloud runs in its own VPC/host and the flow crosses to us over the MXL fabric, the
source is still `mxlsrc` on the LOCAL domain (the proxy lands it here). For non-routed networks
you may need `tools/patch-target-ip.py` (dmf-mxl#714 workaround). See CONTRIBUTION-SPLIT.md Tier 3.

## Wire it (once Q1–Q3 are answered)

```python
from adapters import zoomiso_adapters
# source_flow_ids: the UUID(s) from mxl-info; slot_flow_ids: our guest/zoom slots
adapters = zoomiso_adapters(
    source_flow_ids=[...],                 # from Q1
    slot_flow_ids=['9e111e00-...','9e222e00-...'],
    mode='per_participant',                # or 'composite' from Q1
    timing_policy='align',                 # or 'restamp' from Q3
)
# run one ContributionCore per adapter (same supervisor/watcher as guests)
```
Then the existing `guest_slot_watcher.py` re-attaches the selector automatically — ZoomISO
participants occupy guest slots, cut like any source. Nothing else changes.

## Success = a Zoom participant on the program
Cut to the slot (`/pipeline/active-input {"slot":N}`), confirm the participant is live on the
WebRTC program. That's the first independent, third-party native-MXL contribution interop test —
exactly the offer in the Liminal outreach.

## Plan B — self-host the Zoom capture (if ZoomISO Cloud stalls)

ZoomISO Cloud is Plan A: it emits native MXL, needs no Windows box and no Zoom-SDK approval
gate, and our ingest is already built for it. But a self-host path exists and is proven by a
reference implementation — **[iamfatness/CoreVideo](https://github.com/iamfatness/CoreVideo)**
(OBS plugin, MIT): it pulls raw per-participant **I420 YUV** video + per-participant 48kHz PCM
audio out of a live meeting via the **Zoom Meeting SDK** (5.17.x / 7.x), through a dedicated
`ZoomObsEngine` child process over shared memory / named pipes. That is exactly "do it ourselves."

**The easy 20% (our side):** I420-frames-in-shared-memory → v210 MXL flow is precisely the conform
stage `ContributionCore` already does. A `SourceAdapter` whose `source_fragment()` reads the engine's
shared-memory frames (or a small shim that republishes them) → the rest of the seam is unchanged.
Est. ~1–2 days once the frame format is known.

**The hard 80% (the Zoom SDK, honest):**
- **Gated + proprietary:** needs a Zoom Marketplace app with Public Client OAuth + PKCE + Meeting
  SDK/Embed enabled, and a developer account. Approval process, not a download.
- **Platform mismatch:** CoreVideo's capture engine is C++17/Qt6/CMake against the Zoom SDK —
  **Windows (full) / macOS (beta) / Linux (unsupported).** Our MXL stack is Linux+Docker. So the
  Zoom-capture half runs on a **Windows/Mac contribution host**, not our Linux mixer VMs.
- **Bandwidth:** standard Zoom accounts ~30 Mbps incoming video budget total (Enhanced Media ~100);
  caps how many HD participants you can pull.

**Where it lands architecturally:** this IS the Contribution‖Mixer split (CONTRIBUTION-SPLIT.md
Tier 3) — the Windows/Mac Zoom-SDK box is the untrusted contribution edge; MXL mixer stays Linux;
the fabric is the boundary. So Plan B reuses the isolation design we already have.

**Before committing to Plan B, ask John (CoreVideo author):** Did Zoom approve the SDK app easily?
Does the engine run headless at all? Is the shared-memory frame format stable/documented? Licensing
to reuse his engine (it's MIT, but confirm intent)?

---
*Pipeline proven 2026-10-02 against a synthetic native-MXL source on a cold-clone GCP VM. The
real-ZoomISO unknowns above are the only things left to measure. Companion: CONTRIBUTION-SEAM.md
(the adapter model), CONTRIBUTION-SPLIT.md (where ZoomISO sits in the isolation tiers).*
