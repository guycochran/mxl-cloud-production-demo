<!-- SPDX-FileCopyrightText: 2026 Contributors to the Media eXchange Layer project. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Switcher roadmap from the Oct 2026 MXL spec/SDK review

This plan is the output of a source-level review (Oct 7 2026) of the MXL specs, the
`gst-mxl-rs` v1.1.0 plugin source, the AMWA DMF increments, and the dmf-mxl **v1.2**
roadmap (GitHub milestone #9), compared against this repo. It records *what we should
build/change on the switcher and why*, grouped by dependency.

Sources reviewed:
- `gst-mxl-rs` @ **v1.1.0** — `README.md`, `src/clock.rs`, `src/mxlsink/render_continuous.rs`
- MXL docs — Timing Model, Fabrics
- AMWA increments — IN-001..IN-006 (esp. **IN-005** external-signal ingress)
- dmf-mxl **v1.2** milestone — Timed Data (#327 / PR #720, children #731 #730) and
  device-hosted/CUDA grains (#594/#751/#752)
- `tools/mxl-fabrics-demo/demo.cpp` — the real Fabrics C API

---

## The one finding that reframes our restamp

`gst-mxl-rs` `mxlsink` is **not** a dumb writer. At the source level:

- **`clock.rs`** — each pipeline samples ONE shared constant `D = mxl_now −
  pipeline_clock_now` and shares it across all MXL elements via a GstContext handshake.
- **`render_continuous.rs`** — the sink computes `mxl_ts = buffer.pts + base_time + D`
  then `index = timestamp_to_index(mxl_ts, rate)`. **There is no writer-side
  backward-index guard**: a backwards PTS → a lower index → a write *behind* the
  committed head → the writer rejects it → `-5`. The writer **relies on the caller
  presenting monotonic PTS.**

Consequences for `tools/contribution_core.py::_restamp`:
1. Our monotonic clamp (added Oct 6 for the `-5` loop) is **load-bearing and correct —
   keep it.** It does exactly the job the SDK leaves to the writer.
2. Our restamp **also re-derives its own offset** against `pipe.get_clock()`, and the
   sink then adds its own `D` (same clock class) on top — the PTS is offset-corrected
   twice. They mostly cancel into a constant (that's why it works), but it's fragile.
3. The servo and its diagnostics are hardcoded to 30 fps (`FRAME_NS = 33_333_333`),
   which is why a 60 fps source reports `err=-41s / fps=173`.

---

## Tier 1 — Ship now (fixes real bugs, no upstream dependency)

### 1.1 — Rework the restamp to be rate-aware (the 60 fps bug) ⭐ IN PROGRESS
- **Why:** the open follow-up from Oct 6; now scoped by the `clock.rs` model above.
- **Do:** derive the monotonic frame step from the *flow's* grain rate instead of the
  30 fps `FRAME_NS` constant; keep the conform stage's `videorate → framerate=30/1`
  decimation so the grain cadence stays canonical; fix the diag `fps` so it reflects
  the committed (post-decimation) cadence, not the raw source rate. Keep the monotonic
  clamp untouched.
- **Guardrails:** `tests/test_contribution_core.py` pins the golden launch strings
  (`framerate=30/1`, exactly one `videorate`) and the monotonic re-lock — do not change
  those. This is a probe-logic change, not a pipeline-shape change.
- **HW:** verify on the camera at the lab (not the phone — it drops <90s); confirm a
  60 fps source yields a sane `fps`/`err` and 0 `-5`.

### 1.2 — Pin & verify MXL SDK v1.1.0 (half-done)
- Recorded v1.1.0 in `docs/VERSIONS.md` + `docker/guest-ingest.Dockerfile`. Remaining:
  run the `gst-inspect`/`strings libmxl.so` verify commands against the pinned
  `test-generator` base on the next HW box to confirm (not assert) the version, and
  review the two upstream seam commits (`getFlowReader` flow-id check; `gst-mxl-rs`
  wait-for-missing-flow + audio-reader-spin) before relying on them.

### 1.3 — Audio monotonic floor = sample-duration (latent, low-pri)
- **Why:** `render_continuous.rs` indexes audio per sample; `_restamp_audio` uses the
  33 ms video `FRAME_NS` as its floor (1600 samples of slack). Not a bug today (#36
  HW-proven) — a lip-sync-precision cleanup, best folded into 1.1's HW session.

---

## Tier 2 — Align now (cheap spec/positioning wins)

### 2.1 — IN-005 ingress registry ✅ DONE
- **Why:** IN-005 wants ingress timing adjustments *traceable* via a registry; we only
  `print()` `cadence offset locked / re-synced`.
- **Shipped:** `contribution_core` writes `$MXL_INGRESS_DIR/<flow8>.json` (default
  `/tmp/mxl-ingress`) on lock / re-lock / hard-relock / diag — source caps (provenance) +
  locked offset, running `err_ms`, grain step, hard-relock count. Atomic write, stdlib-only,
  opt-out via empty `MXL_INGRESS_DIR`. Tests + `docs/CONFIG.md` §"IN-005 ingress registry".

### 2.2 — Timing-Model drift criterion as the soak pass/fail ✅ DONE
- **Why:** the Timing Model says latency must stay "low and mostly constant over hours."
  `mxl-info` exposes no transfer latency directly, so derive it from the ingress records.
- **Shipped:** `tools/ingress-soak.sh` samples the IN-005 records over a window and renders
  PASS/FAIL in the spec's own terms — frames advancing (not DRIFT), `|err_ms|` bounded,
  `err_ms` not trending, `hard_relocks` not climbing (no re-lock churn). This is the
  explicit acceptance test for the 60fps restamp HW re-verify.

### 2.3 — IN-001 / IN-004 gap-note refresh
- **Why:** IN-004 (Flow-Connection Phase-2 control API) is now WIP and extends IN-001.
  Map our v1 `/api/mxl/*` control surface to IN-004's direction in
  `docs/IN-001-GAP-ANALYSIS.md` to keep the "conformant control API" positioning current.

---

## Tier 3 — Track & prep for v1.2

### 3.1 — ANC / closed-captions data flow 🟡 CODE DONE, HW-verify owed
- **Why:** v1.1.0's `gst-mxl-rs` already has a worked, tested
  `meta/x-st-2038 ↔ video/smpte291` CEA-608 round-trip. De-risked path to captions /
  SCTE through the switcher today, and the exact thing v1.2 Timed Data generalizes.
- **Shipped (no-VM):**
  - `contribution_core` gained a **third essence: `data`** — a conform branch that enforces
    `CANON_DATA_CAPS = meta/x-st-2038,alignment=frame` + queue → `mxlsink` (writes the
    `video/smpte291` data flow). No restamp (forced `timing_policy=preserve`; ANC grains are
    frame-aligned). `group_hint` knows `:Data`.
  - `CaptionFileAdapter` (adapters.py) mirrors the v1.1.0 README producer chain
    `filesrc ! subparse ! tttocea608 ! ccconverter ! closedcaption/x-cea-608 ! cctost2038anc`.
  - `docker/guest-ingest.Dockerfile` `--build-arg WITH_CAPTIONS=1` (OFF by default so the
    HW-proven image is byte-identical) installs `plugins-bad` (`ccconverter`); it documents
    that `rsclosedcaption` (gst-plugins-rs ≥ 0.14) must still be **built from source**.
  - Tests: data launch string, adapter chain + preserve policy, `:Data` group hint.
- **Still owed (needs HW):** build `rsclosedcaption` into the caption image, then run the
  round-trip (subtitle → data flow → `st2038anctocc` decode) on the lab box and confirm a
  reader recovers the captions. Batch with the next HW session.

### 3.2 — Timed Data (event flows) readiness
- **Why:** PR #720 (`MXL_DATA_FORMAT_EVENT`) is review-complete and near merge —
  a registry-typed, strongly-timestamped home for SCTE-104/35, tally, ADM metadata;
  #731 makes it fabric-portable. Our guest-ingest is a natural first consumer.
- **Do now (cheap):** a thin design sketch for a "guest event-flow sidecar alongside
  A/V"; watch #720/#731/#730 for merge, then be the reference consumer.

### 3.3 — IS-05 connection shim on our NMOS node
- **Why:** `tools/nmos_node.py` is IS-04 discovery-only; its docstring calls IS-05 "the
  next build." NVIDIA's `gst-nmos-rs` confirms IS-05 + IS-08 are tractable but needs the
  `nvnmosd` daemon and is NOT our MXL transport — **blueprint only.** Build the shim
  ourselves on our lightweight BCP-007-03 node. Medium effort; after ANC.

### 3.4 — Watch #232 (GStreamer-plugin alpha support) for the keyer
- Backlog item, not ours to build — alpha in the gst plugin benefits the html5-keyer
  path. Track only.

---

## Fabrics (separate lane — reference, no switcher work implied)

`tools/mxl-fabrics-demo/demo.cpp` clarified the real Fabrics API:
**INITIATOR = sender** (holds an MXL *reader*, pushes grains via
`mxlFabricsInitiatorTransferGrain/Samples`); **TARGET = receiver** (holds an MXL
*writer*, exposes buffers, emits a base64 `TargetInfo`). Provider auto-select is
**EFA > VERBS > TCP > SHM**. This confirms our existing proxy/NAT approach
(`tools/patch-target-ip.py` rewrites the sockaddr inside the base64 `TargetInfo`) is
correct. Any future "fan a guest out to a second node" work rides #731 (Timed Data over
fabric) once it lands.

---

## Sequencing

1. **Tier 1.1 + 1.2 + 1.3** in one HW session (restamp rework is the headline; verify
   SDK + fix the audio floor while the rig is up).
2. **Tier 2.1 + 2.2** (ingress registry + soak criterion) — small, alongside/after.
3. **Tier 3.1 (ANC/CC)** as the next real feature.
4. **Tier 3.2 / 3.3** tracked; act when #720 merges / when IS-05 is prioritized.
