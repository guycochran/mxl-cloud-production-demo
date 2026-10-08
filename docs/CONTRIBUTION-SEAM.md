<!-- SPDX-License-Identifier: Apache-2.0 -->
# MXL Contribution Seam — spec (ZoomISO-ready, prove with SRT today)

**Status:** IMPLEMENTED 2026-10-02 (commit `0e98a2c`, refined same day). The core + the
three conform adapters are live; `ZoomIsoMxlAdapter` is a PLANNED stub (wire at beta).
No cloud spent. Author: live-system/repo lane.
**Goal (from Guy):** make the open MXL-native facility adoptable *and* ready to run the
daily OHG show. Dream path = **ZoomISO Cloud (MXL output)** guests land as first-class
sources; pragmatic path = build the seam now against the **existing SRT guest path** so
ZoomISO Cloud drops in the day the beta arrives. "Work on what we can" — don't block on a
beta we don't control.

**Strategy context:** 3+4 = open-source staple + fill-the-gaps, **adoptable-first**. Incumbents
(Grass Valley ACE-3901-GRID gateway + AMPP, Lawo HOME, Zoom's own cloud service) are bolting
MXL onto heavy iron. The open wedge = a software-only MXL-native stack a small team clones and
runs. This seam is the contribution edge of that stack.

---

## 1. The insight: the ingests already share one back half

Reading `tools/cam_ingest.py`, `cam2_ingest.py`, `guest_ingest.py`, every contribution path is:

```
[TRANSPORT FRONT END]  →  [CANONICAL CONFORM]  →  [RESTAMP]  →  [SINK TO SLOT]  →  [ANNOUNCE]
     varies                identical             identical      identical         identical
```

- **Front end (varies):** `rtspsrc`/SRT + depay + `avdec_h264` (the Makito sends H.264 in the
  shipped profile — HEVC software-decode starved the keyer, switched 2026-09-10), a
  per-path jitterbuffer (`latency=` 20ms wired cam … 200ms guest … 1000ms cellular — SRT
  latency is per-path physics, FINDINGS §9).
- **Canonical conform (identical, load-bearing):** `videorate ! videoscale add-borders=true !
  videoconvert ! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,
  pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709`. `videorate`
  reconciles 30000/1001 → exact 30/1 (without it the v210 caps intermittently fail to
  negotiate — documented crash). `videoscale add-borders` letterboxes any guest aspect.
- **Restamp (identical, load-bearing):** the cadence-preserving PTS offset probe, `MARGIN_NS =
  66_000_000` (2 grains; bigger margins starve readers → "too late" wedges). This is the single
  most important idea in the repo — it's what makes a remote source instantly cuttable against
  local flows.
- **Sink to slot (identical):** `mxlsink domain=… flow-id=<fixed UUID> label=… group-hint=…`.
  Fixed flow UUIDs map 1:1 to selector slots / switcher buttons.
- **Announce (identical):** POST `/api/mxl/repair` on first locked frame so the selector
  re-attaches and the button goes live hands-off (with the 429-cooldown retry loop).

**Today the back half is copy-pasted three times.** The seam = extract it once; make the front
end a pluggable "source adapter." Then adding ZoomISO Cloud = writing one new adapter, not
touching the conform/restamp/slot/announce logic that took weeks to get right.

---

## 2. The abstraction

```
                        ┌─────────────────────────────────────────────┐
  SourceAdapter  ──►    │  ContributionCore (the proven back half)      │  ──►  MXL domain flow
  (pluggable)           │  conform → restamp(+2-grain) → mxlsink → ann. │       (fixed slot UUID)
                        └─────────────────────────────────────────────┘
```

**`SourceAdapter` interface** (as shipped — see `tools/contribution_core.py`):
- `source_fragment() -> str` — the gst-launch FRONT-END fragment (ending `! `). For a conform
  adapter it ends at the decoded queue; for a native-MXL adapter it is the `mxlsrc …` that emits
  v210 grains directly.
- `latency_ms` — the per-path jitterbuffer hint.
- **Two ORTHOGONAL properties the core acts on** (deliberately NOT one "is native" boolean —
  "transport is MXL" and "already on my domain clock" are different questions):
  - `needs_conform: bool` — run the videorate/videoscale/videoconvert/v210 stage? `False` only
    when the source already emits canonical grains.
  - `timing_policy: 'restamp' | 'align' | 'preserve'` — `restamp` (default) maps onto the local
    clock +2 grains; `align` trusts claimed domain alignment (logs "assumed, not verified" — the
    provisional ZoomISO setting, flip to `restamp` if the beta shows a foreign clock); `preserve`
    passes timestamps untouched (only once proven on our clock).
  - `is_native_mxl` survives as a read-only convenience (`== not needs_conform`); it says nothing
    about timing.
- metadata: `label`, `group_hint`, `description`, target `flow_id`/slot, `announce_on_lock`.

**`ContributionCore`** owns everything proven: the conform caps, the restamp probe + MARGIN_NS,
the mxlsink wiring, the announce-on-first-frame + retry. One place to fix bugs, one place that
carries the FINDINGS lessons.

### Adapters (capability status: ✅ VERIFIED · 🔧 IMPLEMENTED · 🧪 EXPERIMENTAL · 📋 PLANNED)

| Adapter | Front end | needs_conform / timing | Status |
|---|---|---|---|
| `SrtGuestAdapter` | mediamtx SRT → rtsp → h264 decode | true / restamp | ✅ VERIFIED (byte-identical to pre-refactor guest_ingest) |
| `RtspCamAdapter` | rtspsrc → h264 decode (+videorate 30000/1001→30/1) | true / restamp | ✅ IMPLEMENTED (cam_ingest) |
| `MakitoAdapter` | rtspsrc → **h264** decode (shipped Makito profile; HEVC retired 2026-09-10) | true / restamp | ✅ IMPLEMENTED (cam2_ingest) |
| `ZoomIsoMxlAdapter` | ZoomISO Cloud MXL flow → (no decode) | false / align* | 📋 PLANNED stub (*align = assumed, MEASURE at beta) |

---

## 3. What to build now (no ZoomISO, no cloud needed to design)

1. **Extract `ContributionCore`** from the three ingest scripts into `tools/contribution_core.py`
   — verbatim move of the conform caps, restamp probe, MARGIN/RESYNC constants, mxlsink setup,
   announce loop. Behavior-preserving; the three existing tools become thin adapters that call it.
   (This is pure refactor — testable on any single VM with the existing SRT path, cheap.)
2. **Define `SourceAdapter`** (ABC) + port `SrtGuestAdapter`, `RtspCamAdapter`, `MakitoAdapter`
   onto it. Prove parity: a guest SRT push still becomes a cuttable button, same ~25ms cuts.
3. **Write `ZoomIsoMxlAdapter` as a documented stub** — `needs_conform=False` + `timing_policy='align'` (provisional), a clear TODO block
   citing the two unknowns we must verify against a real beta flow:
   - does ZoomISO Cloud emit **one MXL flow per participant** or a composite? (determines whether
     it's N slots or one) 
   - is its grain cadence **already domain-aligned** (skip restamp) or does it need the +2-grain
     restamp like any other source? **Do NOT assume — measure on first beta contact.**
4. **Keep the `/api/mxl/repair` announce contract stable** — it's the one coupling to the
   backend; document it in the core so an adopter on a different backend knows what to implement.

## 4. What waits for the beta (don't block)

- Actual ZoomISO Cloud MXL flow spec (per-participant vs composite; cadence; flow-id scheme).
- Whether ZoomISO's flows need the sockaddr/fabric patch (if they arrive cross-host via proxy).
- Routing N Zoom participants → N selector slots (the `ZoomISO Technical Details` OSC/port work
  in CLAUDE.md is the *old* local-ZoomISO path; ZoomISO **Cloud** is a different, MXL-native
  animal — don't carry the port-9091 assumptions over without checking).

## 5. Phase-0 free actions (do before any credit is spent)

- **Get on the ZoomISO Cloud beta** (Guy: signup at liminalet.com/zoomiso-cloud). Everything in
  §4 unblocks the moment we have one real flow to inspect. Earlier in the queue = earlier proof.
- **Post the IN-001 §6.2 issue** (already drafted, docs/IN-001-ISSUE-1-DRAFT.md V3) — free
  standards-contributor credibility; pairs with this seam work as "we build AND we feed the spec."

## 6. Success criteria

- `contribution_core.py` exists; the 3 existing ingests are thin adapters over it; a live SRT
  guest still goes cuttable hands-off with no regression (same cut latency, same restamp behavior).
- `ZoomIsoMxlAdapter` stub compiles and documents exactly what we'll verify at beta — so the
  beta day is "fill in a known shape," not "design under pressure."
- The seam is documented well enough that an *adopter* (not us) could write a new SourceAdapter
  (e.g. NDI-in, WebRTC-in) without reading the FINDINGS saga — the lessons are encoded in the core.

---

*Ties: builds on tools/{cam,cam2,guest}_ingest.py + the FINDINGS restamp/latency lessons.
Part of the adoptable-first + fill-gaps strategy (memory: mxl-readme-dmf-reframe-2026-10 NEXT list).
This is repo-lane work; the DMF-lab site reframe is the website agent's parallel track.*
