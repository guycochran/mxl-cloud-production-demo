# Broadcast-panel UI — design notes

`web/local-broadcast.html` is a **mockup-grade, drop-in replacement** for `web/local.html`: a single
self-contained file (inline CSS + JS, no CDN, no external hosts, no new dependencies) styled like a
hardware production-switcher panel (classic hardware-switcher feel).

> **Status: design mockup to be wired.** Everything the control API already supports is wired with the
> *same endpoints, payloads, token handling and element ids as `local.html`*. Everything the API does
> **not** support yet (real transitions, T-bar, audio levels, recorder, timecode source) is a visual
> mock and is tagged `MOCK` in the UI. Details in [What is wired vs mock](#what-is-wired-vs-mock).

Source of truth for the API: `backend/mxl-routes.js` and `backend/local-server.js`.

---

## 1. Design system

### Fonts (self-hosted only)
Reuses the existing `web/fonts/*.woff2`; nothing new is added.

| Role | Family / weight | Used for |
|---|---|---|
| Labels, buttons, engraved panel titles | **Barlow Condensed** 600 / 700, UPPERCASE, wide tracking | everything "printed on the panel" |
| Numbers, readouts, timecode, LCDs | **IBM Plex Mono** 500 / 600 | TC clock, rate field, pattern LCD, key numbers, status bar |

`@font-face` lists the absolute URL first (`/fonts/…`, what `express.static(web)` serves, identical to
`local.html`) and a relative URL second (`fonts/…`) so the file also renders if opened straight from `web/`.

### Colour
| Token | Hex | Meaning |
|---|---|---|
| `--c0 … --c6` | `#0b0c0d … #4a4e56` | charcoal chassis, panels, bezels (cool neutral greys) |
| `--red` | `#e5484d` | **PGM / on air** — never used for anything else |
| `--grn` | `#23d97c` | **PVW / armed next** |
| `--amber` | `#ffb400` | **selected / pending / option on** (also the LCD & timecode ink) |
| `--ink / --dim / --faint` | `#e4e6ea / #9aa0ab / #69707b` | text on the chassis |
| `--cyan` | `#35e0ff` | demo-only accents (lower-third sub-line, clip progress) |

Red/green/amber are the same values `local.html` already treats as "sacred", so tally colours do not change
meaning between UIs.

### Surfaces
* **Chassis** — vertical charcoal gradient + faint brushed-metal vertical hatch.
* **Panel** — raised plate with inner top highlight, hairline black border, drop shadow, four corner screws
  (`.panel.screws`), engraved title with a rule (`.eng`).
* **LCD / readout** — near-black well, inset shadow, amber mono text with a soft glow (timecode, rate field, pattern select, T-bar %).
* **LED chips** (`.led`) — recessed black pill + round LED (`ok` green, `bad` red, `demo`/`amb` amber, `red` red).

### Button states
Two physical button families: `.key` (square source caps, edge = `--key`) and `.btn` (rectangular function buttons).

| State | Look | Applies to |
|---|---|---|
| unlit | grey bevelled cap, light label | any available source / option |
| **PGM lit** | red cap + red halo, white LED bar | PROGRAM-row key whose slot is on air (`status.input`) |
| **PVW lit** | green cap + green halo, white LED bar | PREVIEW-row key whose slot is armed (`status.pvw`) |
| **amber (selected / pending)** | amber cap + halo | PREVIEW key between click and server ack (`.pend`, ≥240 ms latch); selected option buttons (`.sel`): transition style, Key Off, Hot-punch armed |
| no signal | darker cap with diagonal hatch, `not-allowed` cursor | slot whose `status.slots[].live === false`; arming/cutting to it is refused (same as `local.html`) |
| pressed | cap travels down 3–4 px, shadow collapses | `:active` |
| disabled | dim label, no glow | CUT/AUTO when there is nothing to take |
| focus | 2 px amber outline | `:focus-visible` everywhere |

Special: **CUT** is a white cap when a take is possible (hardware-switcher convention); **AUTO** turns red while its T-bar sweep runs.

### Tally rules (identical to `local.html`)
* A source that is on PGM shows **red** everywhere (tile border/flag, PROGRAM key, PGM monitor frame).
* A source that is armed on PVW shows **green** (tile, PREVIEW key, PVW monitor frame) — except when PVW == PGM (after a take the backend sets `pvw = input`), in which case the tile stays red and both bus rows light the same slot.

### Layout grid
Desktop (≥1241 px wide) — 4 stacked rows inside `.chassis` (CSS grid, `height: max(100vh, 860px)`):

```
┌──────────────────────────── HEADER: facility · timecode · LEDs · ON AIR ────────────────────────────┐
├──────────── MULTIVIEW (1fr) ───────────────────────────────┬──────── SIDE (clamp 290–380 px) ───────┤
│  [ PVW monitor 16:9 ]   [ PGM monitor 16:9 ]  ← side by side│  PATTERN · TEST GEN (select)           │
│  [ 7 source tiles with tally borders, 1 row ]               │  AUDIO meters (mock)                   │
│                                                             │  RECOVER (Prime / Repair)  · TALLY KEY │
├──── SOURCE BUSES (PGM row / PVW row) ───────┬── DSK 1 ──┬── TRANSITION (styles, rate, CUT/AUTO, T-bar) ──┤
├──────────────────────────── STATUS BAR: #msg · key legend · mode ────────────────────────────────────┤
```
* Monitor width = `min(50% − 6 px, (container-height − chrome) × 16/9)` via a CSS container query so the PVW/PGM pair always fits the stage without scrolling.
* Source-button edge `--key = clamp(56px, min(5.6vw, 11.5vh), 112px)` — buttons scale with the window.
* ≤1240 px wide: single-column stack (header wraps, side panels become a 2-column grid, buses/DSK/transition wrap) and the page scrolls vertically. Verified at 1440×900, 1920×1080, 1024×768.
* Slot count comes from `/api/mxl/slots` (7 today); wall/bus grids resize to `N` (wall wraps to two rows above 8).

---

## 2. What is wired vs mock

Every network call below is **unchanged** from `local.html`; the UI shows a green `API` tag on wired panels and an amber `MOCK` tag on mock ones (hide all tags with `document.body.classList.add('hide-tags')`).

| UI element | ids | Wired to | Notes |
|---|---|---|---|
| Boot | — | `GET /api/mxl/status`, `GET /api/mxl/slots` | slots fall back to `status.slots[].role` if `/slots` is missing |
| Status poll (1.5 s) | `#conn #connText` | `GET /api/mxl/status` | `ok`/`bad` classes, text `connected` / `no backend` / `offline`, same as before |
| PGM monitor | `#pgm` | `<iframe src="/program/">` (mediamtx WHEP player via the `/program` + `/mxl2webrtc` proxies) | **unchanged WHEP logic** — only the frame around it is new |
| PVW monitor | `#pvwImg` | `GET /api/mxl/thumbs/<role>.jpg` of the armed slot, refreshed every 1.5 s | the API has no preview *video*; this is a thumbnail, labelled as such. |
| Multiview tiles | `#wall` | `GET /api/mxl/thumbs/<role>.jpg` (probe + `no signal` slate on 404) | click / Enter / Space (when focused) arms preview |
| PREVIEW bus | `#busPvw` | `POST /api/mxl/preview {input:slot}` | optimistic, amber until acked; refuses `live:false` slots |
| **CUT** | `#take` `#takeSub` | `POST /api/mxl/take {}` | same busy/hold-state handling as `local.html` |
| **AUTO** | `#autoBtn` | `POST /api/mxl/take {}` | **the backend can only hard-cut**, so AUTO cuts immediately and animates the T-bar over RATE frames (cosmetic) |
| PROGRAM bus (hot-punch) | `#busPgm` `#hotBtn` | `POST /api/mxl/input {input:slot}` | **new, but locked by default** (see below) |
| DSK Key On / Off / Auto | `#keyOnBtn #keyOffBtn #keyBtn` | `POST /api/mxl/key {on}` | `#keyBtn` (AUTO) toggles `!keyOn`; no key *transition* exists, the key is binary |
| Pattern | `#patSel` | `POST /api/mxl/pattern {pattern}` | list from `status.available`, current from `status.patterns.tg1`. ⚠ Backend **also cuts PGM to slot 2** if not already there — the panel says so; the UI refreshes PGM after it |
| Prime sources | `#primeBtn` | `POST /api/mxl/warmup {}` | |
| Repair program | `#repairBtn` | `POST /api/mxl/repair {}` | rate-limited server side |
| Message line | `#msg` | — | same `⚠` error convention |
| Token | — | `#token=<secret>` → `localStorage.mxl_control_token` → `X-MXL-Token` on POSTs | **copied verbatim** from `local.html` |
| CONNECTED LED | `#conn` | status reachability | |
| SRC n/m LED | `#srcLed` | count of `status.slots[].live` | |
| ON AIR badge | `#onair` | lit when connected **and** `status.input !== null` | derived, not a separate API |
| PGM chip | `#pgmChip` `#pgmV` `#pgmName` `#pvwV` | `status.input` / `status.pvw` + slot labels | |
| Facility name | `#facName` | `<body data-facility="…">` (static) | `config/facility.json` has `facility.name` but no endpoint serves it — easy follow-up |
| Timecode | `#tcHMS #tcFr` | **mock**: browser wall-clock, frames at 30 fps | not MXL/PTP time |
| Transition style Mix/Dip/Wipe, Rate, T-bar | `.styles` `#rate` `#tbar` | **mock** | completing a T-bar stroke (or End key) fires the same `take` |
| REC LED | `#recLed` | **mock** | `n/a` outside demo (no recorder API) |
| Audio meters | `#meters` | **mock** | idle outside demo; needs a levels endpoint (e.g. read the PGM audio flow) |
| Format tag `1080p30`, `1920×1080 · 30` | `#fmtTag #pgmRes` | static text | from `facility.json` |

### Hot-punch lock (new behaviour — please review)
A real switcher's PROGRAM row cuts instantly. A stray click on air is a worse failure than an extra click, so the row
is **locked** by default: clicking a PROGRAM key only shakes it and says so in `#msg`. `HOT-PUNCH LOCK` (or **L**) arms the row for 20 s of idle;
while armed, click or **Shift+1–7** → `POST /api/mxl/input`. Remove the feature by deleting `#hotBtn` and the `hotPunch` function if you don't want it.

### Behaviour differences from `local.html` (intentional)
1. **Enter** now fires **AUTO**, **Space** fires **TAKE**/CUT (before, both took). Both end in `POST /take` today.
2. Added `Shift+n` (hot-punch), `K` (key) and `L` (hot-punch lock); `1–9` still arm preview (limited to the slot count).
3. Preview arming failures now surface in `#msg` and re-sync from `/status` instead of being swallowed.
4. Key buttons check `{error}` in the response before changing local state.
5. When PVW == PGM both bus rows light the same slot (that *is* the backend state).

---

## 3. Demo mode (offline mock)

If the **first** `GET /api/mxl/status` at page load fails (network error, non-2xx, or `{error}`), the page enters **demo mode** instead of an empty UI:

* a hazard-stripe banner "DEMO MODE — simulated…", amber `DEMO MODE` LED (not green CONNECTED), `SIM` tag, `DEMO · SIM` watermark on the PGM monitor, footer says `/api/mxl/* not contacted`;
* an in-page fake facility (`mock` object) answers the same routes/payloads (`preview/take/input/key/pattern/warmup/repair`, incl. the pattern→cut-to-slot-2 side effect, a dead *Guest 2* source and a `source not attached` error path);
* sources are drawn on `<canvas>` (cameras, test generator honouring the selected pattern, clip loop, 2×2 layout, no-signal slate), the DSK draws a lower-third, meters/REC animate;
* **no network requests are made** (verified: zero external hosts in either mode);
* if demo was an automatic fallback it re-checks `/status` every 6 s and reloads into live mode once the backend appears.

Controls: `?demo=1` forces demo (e.g. for design review without a backend); `?demo=0` disables the automatic fallback (recommended for an on-air deployment — set `CFG.ALLOW_DEMO_FALLBACK=false` at the top of the script to make that the default). Demo is **never** entered mid-session: if a live session loses the backend you get the red `OFFLINE`/`NO BACKEND` LED and ON AIR goes dark, not fake data.

---

## 4. How to swap it in

Nothing in this PR touches existing files. To adopt:

1. **Quick A/B** — open `http://<host>:3100/local-broadcast.html` (it is already served by `express.static(web)`; `/fonts/*` resolves the same way).
2. **Make it the default** — either
   * `cp web/local-broadcast.html web/local.html` (keeps `local-server.js` untouched; `git mv` if you want history), **or**
   * in `backend/local-server.js` change `res.sendFile(path.join(WEB_DIR, 'local.html'))` to `'local-broadcast.html'`.
3. `tests/test_local_ui.py` only scans `web/local.html`. Its checks were replayed by hand against this file and pass (no external hosts, no `cdn` substring, only the allowed `/api/mxl/*` routes, all four woff2 referenced, preview/take/thumbs present) — so a straight `cp` over `local.html` keeps `pytest` green. If you keep both files, parametrise those tests over both.
4. Keep `local.html` around for a release as the fallback; nothing else (routes, auth, `facility.json`, the `/program` proxy) needs to change.
5. Smoke test: the page should show `CONNECTED`, tiles with real thumbnails, PGM tally on the live slot; arm a source (key `2`), press **Space**, confirm PGM tally follows.

Element ids preserved from `local.html`: `wall conn connText pgmChip pgmName pgmV pvwV take takeSub keyBtn patSel repairBtn primeBtn msg pgm`.
Changed meaning: `#take` is now the CUT button (still "take PVW to PGM"), `#keyBtn` is the DSK **AUTO** toggle (its text no longer flips; use `aria-pressed`, and `#keyOnBtn/#keyOffBtn` for explicit states).
Everything else is additive.

### Follow-ups to make the mock real (suggested order)
1. `GET /api/mxl/slots` (or `/status`) → add `facility.name` and the format string.
2. A levels endpoint for the PGM/guest audio flows → `meters()`.
3. A recorder status/start/stop → `#recLed`.
4. A real transition in the backend (selector/keyer mix) → replace the cosmetic T-bar sweep in `tbAuto()` and honour `tstyle` + `rate`.
5. A low-latency preview feed (second WHEP path) → replace `#pvwImg`.

---

## 5. Accessibility & keyboard

| Key | Action |
|---|---|
| `1`–`7` (`1`–`9` max, = slot count) | arm that source on **PREVIEW** |
| `Space` | **TAKE** (CUT) PVW → PGM |
| `Enter` | **AUTO** (cut + T-bar sweep) |
| `K` | toggle DSK key |
| `L` | arm / lock the PROGRAM-row hot-punch |
| `Shift`+`1`–`7` | hot-punch cut to that source (only while armed) |
| T-bar focused: `↓/→` +10 %, `↑/←` −10 %, `PgDn/PgUp` ±25 %, `End` complete (takes), `Home` reset | |

* Space/Enter are ignored while a button, slider or select has focus (so keyboard users can activate focused controls normally); after a **mouse** click the button is blurred so Space/Enter go straight back to TAKE/AUTO. Digits, `K`, `L` always work except inside the select/rate field.
* All controls are real `<button>`/`<select>`/`<input>` elements (tiles are `role=button`, T-bar is `role=slider` with `aria-valuenow`, transition styles are a `radiogroup`); bus keys expose `aria-pressed` and `aria-label="Preview: <source>"`.
* `#msg` is `role=status aria-live=polite`; the DEMO state is conveyed by text, not colour alone. Tally is redundantly encoded: colour **and** the `PGM`/`PVW` text flags on tiles and the `PGM`/`PVW` bus labels.
* `:focus-visible` amber outline; `prefers-reduced-motion` stops the on-air pulse, REC blink and T-bar easing.
* Contrast: primary text is light-on-charcoal; the dimmest engraved labels (`--engrave`, `--faint`) are decorative/secondary (not audited with a contrast tool yet).

## 6. Verification done
Rendered headless (Chromium, offline) at 1440×900, 1920×1080, 1024×768; exercised against a throw-away fake control plane (not included) for: status/slots/thumbs boot, arm → Space take, Enter auto, hot-punch locked/armed, dead-source refusal, key toggle, pattern (+cut), T-bar drag/commit, prime/repair, and `#token=` → `X-MXL-Token` on every POST. Verified zero non-same-origin requests in demo, `?demo=0` and live modes. **Not tested** against the real facility/mediamtx (by design — no live-system access).
