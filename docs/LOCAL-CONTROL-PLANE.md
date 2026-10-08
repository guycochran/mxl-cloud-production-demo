<!-- SPDX-License-Identifier: Apache-2.0 -->
# Local control plane — scope

**Status: BUILT (v1 — news-grade switcher).** A self-contained, broadcast-grade
way for an adopter to *drive* the switcher from a browser without the prodbots
backend — preview/program dual-bus with TAKE, a live multiview of every source,
in the repo's house design system.

Shipped:
- `backend/mxl-routes.js` — the open control routes, now with the **dual-bus**
  surface: `POST /api/mxl/preview {input}` arms the preview bus (pure server-side
  state, no device call), `POST /api/mxl/take` cuts the armed PVW to PGM, and
  `GET /api/mxl/status` returns `pvw` + a `slots[]` array with per-slot `live`
  (selector flow-wiring freshness) so the UI can paint PGM/PVW tally and a
  "no signal" state per source. No proprietary dependency — this is the open
  module standing on its own. A cut **pre-warms its destination reader** first
  (the input-selector's reader for a slot is cold until activated, and the first
  activation of a recreated flow shows stale content — the "cut sticks on the
  previous source" wedge). The cut activates the target, waits a beat, then cuts
  for real — one slot, always the destination, so it never flashes other sources
  or blips the WebRTC relay. Tune with `MXL_PREWARM_MS` (default 250ms) or disable
  with `MXL_PREWARM=0`. `POST /api/mxl/warmup` is the heavier full-sweep fallback.
- `backend/local-server.js` — mounts the routes, adds `/api/mxl/slots` (manifest
  labels) and `/api/mxl/thumbs/:name` (serves the per-flow JPEGs, local dir or
  `:8086` proxy; path-traversal guarded), proxies the WebRTC program feed, and
  **binds `127.0.0.1` by default** (set `MXL_CONTROL_BIND=0.0.0.0` for the LAN).
- `web/local.html` — the switcher UI: program monitor + live multiview grid,
  click a tile to arm preview (green), a prominent **TAKE** that names what it
  will cut, per-source live dots, key/pattern/repair. House design system with
  **self-hosted** Barlow Condensed + IBM Plex Mono (`web/fonts/*.woff2`, OFL) so
  the page stays no-external-host / air-gap-clean.
- `backend/package.json` — pins express.

Run: `npm install --prefix backend && node backend/local-server.js` → open
`http://127.0.0.1:3100/`. The multiview needs thumbnails: `scripts/quickstart.sh`
starts them automatically (`MXL_THUMBS=1`, default on) by running
`tools/mxl_thumbs.py` in the `input-selector` container + a `:8086` static server;
without them each tile shows its "no signal" slate but PGM/PVW/TAKE still work.
`quickstart.sh` also launches this UI (`MXL_CONTROL_UI=1`, default on) and prints
the URL + an SSH-tunnel line for driving it remotely.

Verified: the dual-bus flow (slots/status/preview/take) end-to-end against a mock
easy-mxl, no JS console errors, responsive to mobile, fully self-contained (no
external hosts — `tests/test_local_ui.py`). Screenshots: `docs/images/switcher-ui*.png`.

Read [`CONTRIBUTION-SEAM.md`](CONTRIBUTION-SEAM.md) first; this is the control-
plane sibling to the contribution/data-plane docs (the A/V v0.3 scope lives on the
`av-contribution-v0.3` branch until it merges).

---

## 1. Where we actually are (not as coupled as it sounds)

The P1 item reads "de-prodbots backend", but most of the control plane is already
clean:

- **`backend/mxl-routes.js`** — a **portable, mountable** Express module. Manifest-
  driven (reads `config/facility.json` via `backend/facility.js`), no prodbots
  anything. It exposes the whole control surface: `GET /api/mxl/status`,
  `POST /api/mxl/input|key|pattern|repair`. An adopter *already* has the routes.
- **`scripts/quickstart.sh`** — drives the switcher with **raw `curl` to the
  easy-mxl control ports** (`:9604/pipeline/active-input`, etc.) and runs
  `guest_slot_watcher.py` for backend-free selector re-attach. So the quickstart
  tier **can already cut** with no backend at all.
- **`server-enhanced.js`** (gitignored) — the live prodbots backend. Rich (WebSocket
  health history, Anthropic assistant, the full 7-input UI, TAMS, archive host
  routing). NOT shippable and NOT needed by an adopter.

So the gap is narrow and specific: **an adopter has the routes and the raw ports,
but no small runnable server that mounts the routes + serves a page, and no single
"control plane" entry point.** Today they'd either hand-write curl, or lift the
gitignored prodbots server.

---

## 2. The gap, precisely

What an adopter is missing to have a *local* control plane:

1. **A runnable host for `mxl-routes.js`.** The module is `module.exports =
   registerMxlRoutes(app)` — there's no `app` to mount it on in the public repo.
   Need a ~30-line `backend/local-server.js`: make an Express app, mount the routes,
   serve a static control page, listen on a configurable port. Bound to `127.0.0.1`
   by default (the control APIs it proxies are already localhost-only).
2. **A control page that isn't the prodbots kiosk.** `web/mxl.html` exists but is
   wired to the live surface (prodbots URLs, the 7-input live facility, Larix QRs
   pointing at the Azure VM). Need a trimmed **`web/local.html`**: the cut bar +
   status, reading the facility manifest's `selector_inputs` for slot labels, no
   external hosts.
3. **No prodbots fallbacks anywhere in the runnable path.** `mxl-routes.js` already
   honors `MXL_VM_URL` / the manifest; confirm nothing it calls hard-defaults to
   prodbots (the repair announce is already opt-in via `MXL_REPAIR_URL`).

Non-gaps (don't rebuild): the routes, the manifest wiring, the raw-port cutting,
the guest slot watcher. Those are done.

---

## 3. Proposed design

### 3a. `backend/local-server.js` — the missing host
```
const express = require('express');
const registerMxlRoutes = require('./mxl-routes');
const app = express();
app.use(express.json());
registerMxlRoutes(app);                 // the existing portable routes
app.use(express.static(webDir));        // serve web/local.html
app.listen(PORT, '127.0.0.1');          // localhost by default
```
- Port from `MXL_CONTROL_PORT` (default e.g. 3100), bind `127.0.0.1`.
- Only dep is `express`. NOTE: the repo has **no root `package.json`** today (the
  prodbots box provides node deps), so Phase 1 must add a minimal
  `backend/package.json` pinning `express`, or the adopter runs `npm i express`
  first. No WebSocket, no Anthropic, no TAMS — those stay in the prodbots-only
  `server-enhanced.js`.
- The MXL VM address comes from the manifest (`network.mxl_vm`) / `MXL_VM_URL`,
  exactly as `mxl-routes.js` already resolves it. For a single-box quickstart that's
  `127.0.0.1`; add that as a manifest/env case.

### 3b. `web/local.html` — a minimal cut surface
- Slot buttons generated from `config/facility.json` `program.selector_inputs`
  (4-input portable) or `layout_inputs` (full) — labels from the manifest, not
  hardcoded. Cut = `POST /api/mxl/input`. Key toggle, pattern pick, repair.
- The program video: embed the quickstart's existing WebRTC page (`:8889`) in an
  iframe, or just link it. No new media path.
- Inline CSS/JS, single file, no CDNs (same constraints as the repo's other pages).

### 3c. Wire it into quickstart (opt-in)
- `quickstart.sh` gains an optional step: if `node` is present, launch
  `local-server.js` and print `control UI: http://<ip>:<port>`. Skipped cleanly if
  node isn't installed (the raw-curl path still works). `MXL_CONTROL_UI=0` to skip.

---

## 4. Phased plan (each offline-verifiable)

1. **`backend/local-server.js`** + a test that mounts the routes on a fake app and
   asserts the 5 endpoints register (same style as the mxl-routes checks). No live
   MXL needed — the handlers only *call out* when hit.
2. **`web/local.html`** — slot buttons from the manifest; a test that the page has
   no external hosts and references the manifest slot labels.
3. **quickstart opt-in launch** + `--down` teardown of the server; `bash -n` + a
   dry-run that the launch line is well-formed.
4. **Docs** — a QUICKSTART section: "drive it from a browser, no backend".

---

## 5. Open questions
1. **Single-box MXL VM address.** On a quickstart box the easy-mxl control ports are
   on `127.0.0.1`, but the manifest's `network.mxl_vm` is the Azure public IP. Need
   a clean way for the local server to target localhost — probably `MXL_VM_URL=
   http://127.0.0.1` in the quickstart launch, or a manifest `local` profile.
2. **How much UI?** Minimum = cut bar + status. Worth adding the guest-slot map
   (`/tmp/mxl-slot-map.json` the watcher already writes) so an operator sees which
   slot is which. Probably yes — it's cheap and it's the thing that's confusing
   without a UI.
3. **Auth.** Bound to `127.0.0.1` = no auth needed for the single-box case. If an
   adopter exposes it, that's their reverse-proxy's job — document, don't build.

---

## 6. Out of scope
- Rebuilding anything in `server-enhanced.js` (WebSocket health, AI assistant, TAMS,
  archive routing) — those are prodbots-specific, stay gitignored.
- Any new media/data-plane work — this is control only.
- Companion module changes (`companion-module-mxl-switcher/` already talks to the
  same `/api/mxl/*` routes; a local server just gives it a local target).

---

## 7. Success criteria
- An adopter on a quickstart box opens `http://<box>:<port>`, sees the slot buttons
  from their manifest, and cuts the program — with **no prodbots, no external host,
  no hand-written curl**.
- `node backend/local-server.js` runs with only `express` installed.
- The raw-curl path still works for anyone who doesn't want a UI.
