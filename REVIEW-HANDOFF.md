# MXL Switcher — Review Handoff

**For an independent reviewer.** This documents what was built, deployed, and claimed,
with a way to **independently verify every claim** — don't take the assertions on faith,
run the checks.

**Originally written Oct 4 2026** (repo head `7b5d701`, repo then named
`mxl-cloud-production-demo`). **Refreshed Oct 5 2026** — repo renamed to **`mxl-switcher`**
(`github.com/guycochran/mxl-switcher`; the old URL redirects), master head now `9db22c7`.
The sections below the original line are the Oct-4 snapshot (still accurate for the v1
switcher); **the up-to-date state is in "## Session 2 (Oct 5)" immediately after this
header** — read that first.

The author (Claude) got the DNS debugging wrong late in the Oct-4 session and wasted the
operator's time blaming their network before proving external reachability. Treat all
"it works" claims here with appropriate skepticism and verify.

---

## Session 2 (Oct 5) — review fixes, the v1 contract, and a live OHG-skin test

Everything here is on **branches / draft PRs**, nothing on `master`, no live-system changes
from a review branch (per `CLAUDE.md`). The per-PR comments are the live thread; this is the
index.

### A. The independent review (R1–R7) — all addressed, all CI-green
A prior reviewer round found 7 issues. Fixes landed as 5 draft PRs, each CI-green, each with
HW verification where it mattered:
- **#17** — R3 (pattern→role, no longer a magic slot), R4 (strict input validation, null no
  longer cuts to slot 0), R5 (mxlApi AbortSignal timeout → no stuck "busy"), R6c/R6d.
- **#19** — **R2 (the critical adoptability bug):** the UI drove the manifest's FIXED flow
  UUIDs, but a fresh quickstart DISCOVERS different ones → every cut "source not attached" on
  a stranger's box. Fix: `tools/facility_from_discovery.py` generates the manifest from
  discovered flows. **Proven on a genuinely fresh VM** (clone → quickstart → every source
  cuts, program follows).
- **#20** — R7 portable self-healer (selector + relay drift). HW-verified (broke the selector,
  healer restarted it; also caught the relay-waits-for-audio wedge).
- **#22** — **R1 (the cold-reader wedge), now a real fix, not just the pre-warm interim:**
  `MXL_INGEST_PERSISTENT=1` mode in `tools/contribution_core.py` keeps the mxlsink flow alive
  across a source reconnect and rebuilds only the source leg (create-once + swap-reader).
  **HW-verified:** flow kept the SAME inode/ctime across 10 reconnects, cadence continuous,
  zero wedge, program followed. Default path unchanged (no lip-sync regression). A monotonic
  re-lock solves the "grain too early" the naive stabilizer hit. One residual documented
  (a freshly-restamped flow sits ~2 grains ahead of the read head → transient "too early";
  **the stock ingest shows the same**, so it's orthogonal and pre-existing).
- **#23** — R6a (rate-limit keys on the real client via CF-Connecting-IP/XFF, not the shared
  tunnel IP), R6b (per-client failed-auth throttle), R6e (scrubbed live specifics to
  placeholders).

VERIFY: `gh pr list`; `gh pr checks <n>`; for the HW claims, bring a VM up and re-run the
frame-grab (see Oct-4 §4). All five consolidate cleanly (trial-merged, 0 conflicts).

### B. The `/api/mxl/v1` contract — sketched, then IMPLEMENTED + HW-validated (#24, #25)
- **#24** (reviewer's branch) proposes a **core-and-skins** architecture: the open repo is the
  neutral "bones" (sources/slots/cut/take/control API); org-specific UIs are replaceable
  "skins" over a versioned API. My review reply: concept approved; scope `core-v1.0.0` to a
  minimal contract + 2 reference skins; defer candidates/audio/layouts/SSE to additive v1.1+.
- **#25** — the `/v1` contract. Started as schemas (`contracts/v1/*`, `docs/V1-CONTRACT.md`),
  now also **implemented**: `backend/mxl-routes-v1.js` — thin wrappers over the existing
  handlers, with **stable opaque source ids** (`src_<8hex of flow UUID>`; the UUID is the join
  key, fixing the R2 per-box problem), a `health` object, a structured error model, and
  `/v1/meta` capability discovery. Legacy `/api/mxl/*` stay as aliases. 6 new node tests
  (18 total, CI-green).
  VERIFY (code): `backend/mxl-routes-v1.js`, `tests/js/mxl-routes-v1.test.js`.
  VERIFY (HW, done once then VM deallocated): a browser UI client drove the live `/v1` on a
  real switcher — `/v1/cut` moved the real MXL selector (active_input followed), program video
  followed the cut (grabbed the real selector-output frame), bogus id → `{error:{code:
  "unknown_source"}}`. ⚠️ **Gating dependency:** this branch lacks #19, so on a fresh box the
  shim loads baked-in UUIDs that don't exist → merge #19 first (or together) before tagging.

### C. Repo rename + the OHG skin (private, out of scope for this repo)
- Repo renamed `mxl-cloud-production-demo → mxl-switcher` (discoverability; matches
  `mxlswitcher.com`). In-repo clone/URL refs updated; local working dir intentionally
  unchanged (live systemd units reference it).
- A first real **skin** (an Office-Hours "hand-raise director") was built and validated live
  against the `/v1` contract — but it lives in a **private downstream repo**, NOT here, because
  it's organization-specific (exactly what the core-and-skins boundary keeps out of the open
  core). Reviewer does not need it; mentioned only so the "a skin drove `/v1` live" claim in
  (B) has context. #21 is a reviewer design mockup that imports OHG/hand-raise specifics into
  the open repo — flagged (comment on #21) as belonging in the private skin instead.

### Current PR map
#16 reviewer channel · #17/#19/#20/#22/#23 my fixes (all CI-green, draft) · #18/#21 reviewer
design mockups · #24 core-and-skins architecture · #25 the `/v1` contract+shim. VMs all
deallocated (nothing billing).

---

## What this is
`web/local.html` + `backend/local-server.js` + `backend/mxl-routes.js` — a self-contained
"news-grade" vision switcher for an EBU MXL facility. Preview/Program dual-bus with TAKE,
a live multiview grid, driven entirely through the open `/api/mxl/*` routes (no proprietary
`server-enhanced.js`). Goal was an adopter-grade switcher a news org could run.

## Where everything is
<<<<<<< Updated upstream
- **Repo:** `~/Projects/mxl-cloud-production-demo`, branch `master`, pushed to
  `github.com:guycochran/mxl-switcher`. Everything is committed (verify:
  `git status` clean, `git log --oneline origin/master..HEAD` empty).
=======
- **Repo:** `github.com/guycochran/mxl-switcher` (local working copy still at
  `~/Projects/mxl-cloud-production-demo` — the dir wasn't renamed because live systemd units
  reference that path; the GitHub repo IS renamed and the old URL redirects). Everything is
  committed (verify: `git status` clean, `git log --oneline origin/master..HEAD` empty).
>>>>>>> Stashed changes
- **Deployed service:** `mxl-switcher-ui.service` (systemd **user** unit on the prodbots
  home box, `~/.config/systemd/user/`). Runs `node backend/local-server.js` on
  `127.0.0.1:3100`. Verify: `systemctl --user status mxl-switcher-ui`.
- **Public URL:** `https://mxl-switcher.cochran.cloud` → system cloudflared
  (`/etc/cloudflared/config.yml`, tunnel UUID `d27cb70b...`) → localhost:3100.
- **Facility:** Azure VM `mxl-lab` (`20.64.205.144`, RG `OHG-MXL-LAB`). ⚠️ **BILLING WHILE UP** —
  deallocate when review is done: `az vm deallocate -g OHG-MXL-LAB -n mxl-lab`.

---

## CLAIMS + how to verify each independently

> _Oct-4 snapshot below — preserved as-is. Counts/claims were true at `7b5d701`; Session 2
> added tests (now 18 node incl. the `/v1` + R1 suites, more python). The "where everything
> is" block above is still current. Run the VERIFY commands for live numbers._

### 1. Tests pass
CLAIM: 102 Python + 12 Node tests pass (at Oct-4 head; more now — run the command).
VERIFY: `cd ~/Projects/mxl-cloud-production-demo && python3 -m pytest -q && node --test tests/js/*.test.js`

### 2. The switcher is self-contained (no external hosts — the air-gap promise)
CLAIM: `web/local.html` loads nothing from a CDN; fonts are self-hosted woff2.
VERIFY: `grep -oE "https?://[a-z0-9.\-]+" web/local.html | grep -v w3.org` → should be EMPTY.
  Fonts: `ls web/fonts/*.woff2` (4 files) + `web/fonts/LICENSE` (OFL).

### 3. The canonical-slot-space fix (status[] keyed to layout, not selector wiring)
CLAIM: the UI tally paints the right tile regardless of how the selector is wired, because
`mxl-routes.js` resolves pgm/pvw/live by matching flow UUIDs, not array index.
VERIFY: read `backend/mxl-routes.js` `mxlStatus()` + `_layout` + `toLayoutSlot()` /
  `mxlSetInput()`. This was a REAL bug found on HW (tally landed on "Studio Cam 1" while a
  guest was live on program). Scrutinize the index translation.

### 4. The cold-reader wedge fix (THE one to scrutinize hardest)
CLAIM: a cut used to "stick on the previous source" (selector active_input moved, UI tally
moved, thumbnails moved — but the program VIDEO stayed on the old source). Root cause: the
input-selector's reader for a slot is COLD until activated; the first activation of a
recreated flow shows stale content. FIX: `mxlSetInput` pre-warms ONLY the destination slot
(activate target → wait MXL_PREWARM_MS=250ms → activate again for the real cut). The full
`/api/mxl/warmup` sweep is kept as a fallback (it blacked out the WebRTC relay, so it's NOT
the per-cut path). Also a best-effort keyframe nudge to :9601.
VERIFY (code): read the pre-warm block in `mxlSetInput`. Both activations target the SAME
  destination (never flashes other sources). `MXL_PREWARM=0` disables.
VERIFY (HW, the real proof): with the VM up + two live sources, cut A→B→A and grab the
  ACTUAL program output frame (not the WebRTC browser monitor, which lies/freezes). The
  truth-check command, run inside the hls2mxl container on the VM:
    gst-launch-1.0 -q mxlsrc domain=/mxl-domain video-flow-id=5c73394e-85df-50a3-8988-5edde5b5522a \
      num-buffers=8 ! videoconvert ! videoscale ! video/x-raw,width=320,height=180 ! jpegenc \
      ! multifilesink location=/tmp/pg.jpg
  Author's result: A-B-A-B-A 5× in a row, program followed every cut, zero wedges, no manual
  prime. Frame sizes cleanly alternated by source. ⚠️ REVIEWER: re-run this yourself — this
  is the headline correctness claim and it's worth independent confirmation.
  ⚠️ CAVEAT the author flagged: the keyframe nudge targets :9601 (encoder) but the live
  facility's WebRTC relay is the mxl2webrtc CONTAINER's internal :9600 — so that nudge is a
  no-op on this facility. The author claims the pre-warm (not the nudge) is what fixes it.
  Worth confirming the pre-warm ALONE is sufficient (disable the nudge, re-test).

### 5. Auth / security (from PR#15, reconciled)
CLAIM: every mutating route (input/preview/take/warmup/key/pattern/repair) requires
`MXL_CONTROL_TOKEN` when set; `GET /status` is open; `/repair` is rate-limited. Public deploy
has the token ON.
VERIFY (code): `grep -nE "app\.(get|post)\('/api/mxl" backend/mxl-routes.js` — every POST
  should have `auth`; GET /status should not.
VERIFY (live, from the box):
    curl -sI https://mxl-switcher.cochran.cloud/ | head -1                         # 200 (page open)
    curl -s -o/dev/null -w "%{http_code}\n" -XPOST -d '{"input":0}' \
      --resolve mxl-switcher.cochran.cloud:443:104.21.74.205 \
      https://mxl-switcher.cochran.cloud/api/mxl/input                             # 401 (no token)
    # with token (from ~/.env.stack MXL_CONTROL_TOKEN) -> NOT 401 (200 if VM up, 502 if down)
  NOTE: control API is unauthenticated-by-default by design; the token is a shared secret
  over HTTPS, not a real auth system. The security boundary is "view open, control gated."

### 6. External reachability (the thing the author got wrong, then proved)
CLAIM: `https://mxl-switcher.cochran.cloud` is reachable from the public internet.
VERIFY (independent, off this box AND off the operator's network):
    curl -s "https://api.hackertarget.com/httpheaders/?q=https://mxl-switcher.cochran.cloud/" | head -1
  → `HTTP/1.1 200 OK`. The DNS record resolves on 12+ public resolvers + both authoritative
  Cloudflare NS + Google/Cloudflare DoH (status 0). The operator's browser showed NXDOMAIN —
  that is a STALE NEGATIVE CACHE on their PC/router (the record was created ~19:15 UTC and
  their resolver cached the pre-existing "doesn't exist"). Author's handling of this was poor
  (blamed the user repeatedly before proving external reachability). The deployment itself
  is correct; the client-side cache is the only blocker for that one machine.

---

## Facility-side fragility observed during review (NOT switcher-code bugs, but real)
- **WebRTC relay waits for a missing audio flow.** The mxl2webrtc relay defaults to
  `mode: video+audio` and will sit on "waiting for flow to be created" forever if the
  audio_pgm flow (`a0d10000`) isn't running — the program monitor then shows "stream not
  found" even though video is being produced. Fix: start the relay video-only
  (`audio_flow_uuid: null, mode: video`). A production deploy should either always run
  audio_pgm or pin the relay to video-only.
- **Selector pipeline can stop on a facility hiccup**, leaving `running:false, inputs:[]` —
  cuts then 409 ("source not attached"). Needs a restart + re-wire of the cam inputs. The
  live mxlswitcher.com facility has watchdog services for this class (selector-doctor);
  this ad-hoc VM bring-up does not.
- **Fresh-selector settling window:** right after the selector pipeline is RECREATED, the
  first cut's program output can lag a few seconds before catching up (the output reader is
  cold). The per-cut pre-warm handles a stable selector; a just-recreated one is a harder
  case. Worth the reviewer's attention — is a selector-recreate warmup needed?

## Known limitations / open items the reviewer should weigh
- **WebRTC program monitor is flaky**: autoplay-blocks in headless browsers, and the
  mxl2webrtc relay occasionally needs a manual re-lock after heavy cutting or a warmup sweep.
  The CUTTING works; the in-browser VIDEO preview is the weak part. Candidate v2 work: a
  self-healing player + targeting the relay's keyframe endpoint.
- **Per-cut pre-warm adds ~250ms latency** to a source-change cut (only when changing slots).
  Acceptable for correctness; a broadcast purist may want it tighter.
- **Facility bring-up is manual/scripted** (SSH + docker exec sequences), not one-command on
  this VM. The repo's `scripts/quickstart.sh` is the adopter path; the live VM uses ad-hoc
  bring-up this session.
- **The author could not produce a clean external browser screenshot** of the live page
  through its tooling (every external fetcher it reached had its own stale DNS cache). It
  relied on force-resolved curl + hackertarget's API. A reviewer on a clean network should
  just open the URL.

## Suggested reviewer checklist
1. Clone fresh, run both test suites.
2. Read `mxl-routes.js` end-to-end — especially slot-space translation + the pre-warm.
3. With the VM up, re-run the A→B→A frame-grab test (#4) independently. This is the claim
   that matters most and is the easiest to over-trust.
4. Hit the public URL from a clean network; confirm 200 + 401-without-token + token works.
5. Sanity-check the auth model is actually what you'd want for a public control plane.
6. When done: `az vm deallocate -g OHG-MXL-LAB -n mxl-lab` (stop billing).
