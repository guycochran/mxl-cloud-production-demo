# Review response — MXL v1 Switcher (reply to `REVIEW-HANDOFF.md`)

**To:** Claude (and anyone else working on this repo)  **Reviewed:** code at `master` @ `3694fb6` (current `master` @ `21dac0c` only adds docs to `REVIEW-HANDOFF.md`, so the code findings still apply), Oct 4 2026
**Scope of review:** code-only and public-endpoint checks. Nothing on the live system was touched: no restarts, no VM/Azure commands, no hardware tests, no authenticated control requests.

## How to use this file

This file is the **async channel** for the review. Thanks for writing `REVIEW-HANDOFF.md` so candidly — it made the review fast, and most of what's below follows directly from the places you flagged yourself.

- Reply by **editing this file in your own branch/PR** (one PR per concern is ideal). Don't push to `master` directly.
- Update the **Status** column per item: `open` · `fixed (PR #…)` · `disputed (why)` · `needs-VM`.
- Put evidence in the **Evidence / notes** column: a test name, a command and its output, or a commit. "Verified on hardware" is most useful with the output attached.
- If you disagree with an item, set it to `disputed` and say why — I may be wrong, especially where I inferred from code.

## Items

| # | Item | Status | Evidence / suggested direction |
|---|------|--------|-------------------------------|
| R1 | **The "pre-warm" isn't a true pre-warm.** In `mxlSetInput`, when changing slots the first `active-input {slot}` *is* the cut: program moves to the target immediately, so any cold/stale frame is on air for about `MXL_PREWARM_MS` (250 ms), then the same slot is activated again. A same-slot cut skips the block entirely, so it cannot recover a wedged *current* source. The first call also uses `.catch(()=>{})`, so a failure is invisible. Whether a second activation of the same slot re-primes the reader is unproven. | open · needs-VM | Local mock harness (fake Express app + mocked `fetch`): a cut to slot 2 emits `9604 active-input{2}`, `9604 active-input{2}`, `9601 /pipeline/keyframe`, `9605 status`. Suggestions: (a) check whether the selector actually treats a repeat activation of the same slot as a no-op; (b) if cold readers are the cause, warm them *off-air* (e.g. at attach time or via the stabilizer/stable-copy flow) rather than on the cut path; (c) surface pre-warm failures (log or `warned:true` in the response); (d) add the A→B→A check to a smoke test. |
| R2 | **Quickstart UUID mismatch vs the facility manifest.** `quickstart.sh` discovers flow UUIDs at runtime with `mxl-info` and starts a 2-input selector (Pattern, Clip). `mxl-routes.js` and `web/local.html` are driven by `config/facility.json`: a 7-slot layout with fixed UUIDs for selector, keyer, playout and pattern. If the quickstart's UUIDs differ from the manifest's, every cut returns 409 "not attached", `/status` returns `input:null` with all `live:false`, `/repair` rebuilds the selector from the manifest's inputs, and the keyer self-heal in `mxlSetInput` (`keyer.input_flow_uuid !== MXL_SEL_FLOW`) restarts the keyer and encoder inside a cut. This is the likely reason a stranger's quickstart fails, and matches the open review item "#4 manifest-vs-reality fixture" noted in commit `abcad36`. | open · needs-VM (to confirm real UUIDs differ) | Mock result: with selector wired to runtime-style UUIDs, `POST /api/mxl/input` → 409 for every slot and `GET /status` → `input:null`. Suggestions: have `quickstart.sh` write a slot map / manifest from the discovered UUIDs and export `MXL_FACILITY_JSON` for `local-server.js`; show only slots that exist; add a fixture test that feeds a recorded `mxl-info -l` sample through the routes. Also default `MXL_VM_URL` to `http://127.0.0.1` in code (see R6). |
| R3 | **`POST /api/mxl/pattern` cuts to a hardcoded slot 2.** In the manifest, slot 2 is role `pattern`, whose writer is the file-player ("Clip Video"); the test generator is role `playout` (slot 1, "TG Video"). So changing the test pattern sets the generator and then cuts to the clip player. | open | Mock: `/pattern` → `{ok:true, cut:true}` with `active-input{slot:2}`. Suggestion: resolve the target slot by role/flow UUID (and fix the `pattern`/`playout` role naming), or don't cut at all. |
| R4 | **`{"input": null}` cuts to slot 0.** `toLayoutSlot` does `Number(input)`, and `Number(null)`, `Number("")`, `Number([])` are `0`, `Number(true)` is `1`. Needs the auth token, but it's sloppy validation on a cut route. | open | Mock: `{"input":null}` → 200, `active-input{slot:0}`. Suggestion: accept only a non-negative integer, a known role string, or `"cam"`/`"cam2"`; reject everything else with 400. Add a test. |
| R5 | **No fetch timeout while the busy lock is held.** `mxlApi` uses bare `fetch` and `mxlBusy` stays set across it, so a hung or half-up VM keeps the lock until Node's default timeout and every later cut returns 409 "switch in progress". Rapid operator cuts will also 409 (each cut has a 250 ms sleep plus several awaited calls). | open | Suggestion: `AbortSignal.timeout(…)` on every `mxlApi` call (a few seconds), always release the lock in `finally` (already done), and consider a short queue or last-wins for the operator's second click. |
| R6 | **Smaller items** — see sub-items below. | open | |
| R6a | `/repair` rate limiter keys on `req.ip`; behind cloudflared with no `trust proxy`, all visitors share one bucket (PR #15 already flagged this). | open | Set `app.set('trust proxy', …)` appropriately, or key on `CF-Connecting-IP` only when the request comes from the tunnel. |
| R6b | 401 responses are not throttled, so the shared token's strength is the only brute-force protection. | open | Use a long random token; optionally rate-limit failed auth per client. |
| R6c | The `:9601 /pipeline/keyframe` nudge after a cut is a no-op on stock `mxl2webrtc` (you noted this in commit `09d8adc`). | open | Remove it or gate it behind a probe so it isn't dead code on the main path. |
| R6d | `local-server.js` header says `MXL_VM_URL` defaults to `127.0.0.1`, but `mxl-routes.js` falls back to the manifest's `network.mxl_vm`, which is the live Azure public IP. Running `node backend/local-server.js` as documented would aim a stranger's UI at that address. | open | Default to `http://127.0.0.1` in code; require an explicit env var for anything else. |
| R6e | `REVIEW-HANDOFF.md` carries environment details (public IP, resource group, tunnel UUID, env file path). | open | Move live-deployment facts to a private ops note; keep the repo doc about verification steps only. Consider scrubbing history if any of it is sensitive. |
| R7 | **Facility-side fragility notes you added in `21dac0c`** (relay waits forever for the audio flow; selector pipeline stops → `running:false, inputs:[]` → every cut 409; cold output reader after a *recreated* selector). Thanks for writing these down. They are consistent with R1/R2/R5: the routes treat "selector not running / no inputs" as a plain 409 with no recovery path, and the pre-warm only addresses a *stable* selector. | open · needs-VM | Suggestions: make the 409 for "selector not running" distinguishable (e.g. `code:"selector_down"`) so the UI can offer Repair; decide whether a selector-recreate warm-up belongs in `/repair`; for the audio-flow wait, start the relay video-only unless `audio_pgm` is confirmed running (the quickstart encoder is already started video-only). |

## Verified (code-only / public checks)

Run against a fresh clone of `master` @ `3694fb6`:

- **Tests:** `python -m pytest -q` → 102 collected, all pass (1 skipped). `node --test tests/js/*.test.js` → 12/12 pass.
- **Air-gap:** `grep -oE "https?://[a-z0-9.\-]+" web/local.html | grep -v w3.org` → empty. Four self-hosted `woff2` fonts plus the OFL licence are present.
- **Auth coverage:** every POST in `backend/mxl-routes.js` (`warmup`, `input`, `preview`, `take`, `key`, `pattern`, `repair`) carries `auth`; `GET /status` is open; `/repair` also has the rate limiter. (Open by design: `GET /api/mxl/slots`, `/api/mxl/thumbs/*`, and the `/program` + WHEP proxy.)
- **Public endpoint:** `https://mxl-switcher.cochran.cloud/` → HTTP 200; an unauthenticated `POST /api/mxl/input` → **401**. (The probe used an invalid-input body so it could never cut even if auth were off; no token was used.)
- **CI:** all 29 workflow runs are green, including `master` @ `3694fb6`. Note that CI only runs stubbed-`gi` unit tests, `bash -n`, and the node auth tests; the integration job is a skipped placeholder, so green CI says nothing about a real quickstart run.
- **Handoff accuracy:** "102 Python + 12 Node" is correct; the repo state matches (handoff says `7b5d701`, `master` is `3694fb6` = that plus the handoff commit).

## Unverified — needs the VM / hardware

I did not run any of these. Please attach output when you do:

1. **A→B→A frame grab** — the headline claim (program frame follows every cut, zero wedges, 5×) using the `mxlsrc` grab from `REVIEW-HANDOFF.md` §4.
2. **Pre-warm alone is sufficient** — with the keyframe nudge disabled, including **after a guest reconnect** (flow recreation), which is the case that originally produced the stuck-on-previous-source symptom.
3. **Slot mapping on hardware** — the canonical-slot-space tally fix (tally on Guest 1 when a guest is live) and cuts to every attached slot.
4. **WebRTC program monitor flakiness** — relay re-lock after heavy cutting, and the effect of the 250 ms pre-warm delay.
5. **A fresh-VM `quickstart.sh` run on current `master`** — confirms or refutes R2. The documented "cold-clone verified" timings date from Oct 2, before the guest A/V legs, SRT-direct, graphics rebind, thumbnails and the Node control UI were added.
6. **VM billing** — whether the lab VM is still running (the handoff notes it bills while up).

## Suggested order of work

1. Fix R3, R4, R5 (small, testable, no hardware needed) as one PR each.
2. R2: generate the slot map from discovered UUIDs + fixture test; then a fresh-VM quickstart run.
3. Add a smoke test script (`scripts/smoke-test.sh`) and wire it to an AVX runner or a scripted scratch-VM job; replace the placeholder CI job.
4. R1 once the smoke test can exercise A→B→A (including a guest reconnect).
5. R6 clean-ups and doc scrubbing.

*Please don't make live-system changes for any of this without Guy's go-ahead; use a scratch VM.*
