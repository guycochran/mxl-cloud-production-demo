# MXL v1 Switcher — Review Handoff

**For an independent reviewer.** This documents what was built, deployed, and claimed,
with a way to **independently verify every claim** — don't take the assertions on faith,
run the checks. Written Oct 4 2026. Repo head at handoff: `7b5d701`, branch `master`.

The author (Claude) got the DNS debugging wrong late in the session and wasted the
operator's time blaming their network before proving external reachability. Treat all
"it works" claims here with appropriate skepticism and verify.

---

## What this is
`web/local.html` + `backend/local-server.js` + `backend/mxl-routes.js` — a self-contained
"news-grade" vision switcher for an EBU MXL facility. Preview/Program dual-bus with TAKE,
a live multiview grid, driven entirely through the open `/api/mxl/*` routes (no proprietary
`server-enhanced.js`). Goal was an adopter-grade switcher a news org could run.

## Where everything is
- **Repo:** `~/Projects/mxl-cloud-production-demo`, branch `master`, pushed to
  `github.com:guycochran/mxl-switcher`. Everything is committed (verify:
  `git status` clean, `git log --oneline origin/master..HEAD` empty).
- **Deployed service:** `mxl-switcher-ui.service` (systemd **user** unit on the prodbots
  home box, `~/.config/systemd/user/`). Runs `node backend/local-server.js` on
  `127.0.0.1:3100`. Verify: `systemctl --user status mxl-switcher-ui`.
- **Public URL:** `https://mxl-switcher.cochran.cloud` → system cloudflared
  (`/etc/cloudflared/config.yml`, tunnel UUID `d27cb70b...`) → localhost:3100.
- **Facility:** Azure VM `mxl-lab` (`20.64.205.144`, RG `OHG-MXL-LAB`). ⚠️ **BILLING WHILE UP** —
  deallocate when review is done: `az vm deallocate -g OHG-MXL-LAB -n mxl-lab`.

---

## CLAIMS + how to verify each independently

### 1. Tests pass
CLAIM: 102 Python + 12 Node tests pass.
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
