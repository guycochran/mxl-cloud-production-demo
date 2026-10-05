# Review resolution — R1–R7 all addressed, on `master`

Reply to the thread in `docs/REVIEW-RESPONSE.md` (PR #16). Thank you for the review —
every item is now fixed and merged to `master`, and the three `needs-VM` items were
verified on live hardware (Azure `mxl-lab`, Oct 5 2026). This doc is the index; the code
is on `master`.

| # | Status | Resolution |
|---|--------|------------|
| **R1** pre-warm isn't a true pre-warm | **fixed + HW-verified** | `#22` (R1) merged `c89b661` — `MXL_INGEST_PERSISTENT` keeps the mxlsink flow alive across a source reconnect and rebuilds only the source leg (off-cut-path), the proper fix over the interim pre-warm. HW: program followed every cut A-B-A-B-A, real flow-frame grab, zero wedges. |
| **R2** quickstart UUID mismatch vs manifest | **fixed + HW-verified** | `#19` (R2) merged `1d026ee` — `tools/facility_from_discovery.py` generates the manifest from discovered flows; `quickstart.sh` wires it via `MXL_FACILITY_JSON`. **HW: reproduced the bug** (fresh-box backend cut → 409 "playout not attached" while raw selector worked), then **confirmed the fix** (same fresh clone of fixed master → backend cut 200, program video followed). |
| **R3** `/pattern` cuts to hardcoded slot 2 | **fixed** | `#17` merged (`35bc422`,`62da6ab`) — `/pattern` sets the generator and **no longer auto-cuts**; role naming corrected. |
| **R4** `{"input":null}` cuts to slot 0 | **fixed** | `#17` merged — `toLayoutSlot` now rejects `null`/`""`/`[]`/`true`/floats/`NaN` → `-1` → 400. Role names + `cam`/`cam2` aliases + non-negative integers only. Tests added (`tests/js/mxl-routes.test.js`). |
| **R5** no fetch timeout while busy lock held | **fixed** | `#17` merged — `AbortSignal.timeout(...)` on every `mxlApi` call; lock released in `finally`. Node-20-deterministic tests (`7101e46`). |
| **R6a** rate limiter keys on shared tunnel IP | **fixed** | `#23` merged `4537841` — `clientKey` prefers `CF-Connecting-IP`/XFF (gated by `MXL_TRUST_PROXY_HEADERS`); `/repair` limiter now buckets per real client. Tests in `tests/js/mxl-auth.test.js`. |
| **R6b** 401s not throttled | **fixed** | `#23` merged — per-client failed-auth throttle (`MXL_AUTH_FAIL_MAX` → 429), per-CF-IP so one attacker can't lock out others; a correct token clears the count. |
| **R6c** `/pipeline/keyframe` nudge is dead code | **fixed** | `#17` merged — the no-op nudge removed/gated. |
| **R6d** `mxl-routes.js` falls back to the live Azure IP | **fixed** | Redaction (`789ba22`, PR #27) + `#17`: `MXL_VM_URL` defaults to `127.0.0.1` in code; the manifest's `mxl_vm` is now a placeholder, not a live IP. |
| **R6e** `REVIEW-HANDOFF.md` carries env details | **fixed** | `#23` (R6e) + redaction: the public URL, VM IP, resource group, tunnel UUID, SSH key path, and env-file location now live in a *private ops note, not this repo*. Real facility IPs scrubbed repo-wide (RFC 5737 placeholders / required env). History scrub (`filter-repo`) remains an option if warranted. |
| **R7** facility-side fragility (selector down, relay waits for audio) | **fixed + HW-verified** | `#20` (R7) merged `dff3688` — `tools/mxl-selfheal.sh` restarts a stopped selector and catches the relay-waits-for-audio wedge. Also `#28` (`587f44e`) adds a report-only desired-vs-actual drift check (`desired_state_report.py`) that flags exactly these states (incl. UNIQUE_FPS, so it catches "green-but-frozen"). |

## Also landed this round
- **`core-v1.0.0` tagged** — `/api/mxl/v1` versioned contract (stable `src_` ids, health, structured errors, `/v1/meta`). HW-validated live (`/v1/cut` by stable id A→B→A, bogus id → `unknown_source`). `#24` architecture note (`fcb6381`) explains the core-and-skins model behind it.
- **Facility-identifier redaction** across the whole repo (the leak was in our original build commits, not the review branches — you *caught* it in R6d/R6e).

## New finding from the HW session (worth your eye)
`bring-up-mxl.sh` (production) only `docker start`s containers; `quickstart.sh` is the sole
provisioner. Layering bring-up over a quickstart facility produced a **"green-but-frozen"**
state: control plane + kiosk serve 200, flows exist in the domain, but program video doesn't
flow end-to-end. Consistent with your R1/R2/R7 observations — process-up ≠ media-healthy.
The two topologies aren't meant to compose; clean quickstart alone verifies program video.

Suite on `master`: **131 pytest + 31 node**, all green. VMs deallocated.
