# Broadcast skin — design notes

`web/switcher.html` is the **broadcast reference skin** over the versioned core contract
(`core-v1.0.0`). It is a single self-contained file (inline CSS + JS, no CDN, no external
hosts, no new dependencies) styled like a hardware production-switcher panel, and it drives
**only** the open core API `/api/mxl/v1/*`.

It is the blend of two earlier reference UIs:
- the broadcast **chassis** (dual PVW/PGM monitors, multiview + tally, source buses, engraved
  panels, the Barlow Condensed / IBM Plex Mono type system) from the original broadcast-panel
  mockup, and
- the **honest interaction model** from `web/local.html` (arm → take, keyboard, optimistic
  latch, state strictly from the API, URL-fragment token handling).

> **Honesty rule (the point of this skin).** A control appears **only** for a capability the
> core advertises in `GET /v1/meta`. Nothing on the panel implies a feature the core does not
> actually expose. That is what makes it credible to a broadcast engineer: every button does
> a real thing, backed by the versioned contract. The live capability list is shown in the
> **Core** panel so you can see exactly what the connected core supports.

**What was removed from the mockup** (and why — the core does not expose these, so faking them
would mislead):
- **Audio meters** — the core has no level/metering API. Removed (no simulated levels).
- **T-bar, transition styles (MIX/DIP/WIPE), AUTO, RATE** — the core performs a hard cut only;
  there is no mix/effects engine. Removed; a single **Take** is the commit action.
- **REC / recorder LED** — no recording API. Removed.
- **Timecode source** — the clock is the browser wall-clock, labelled **LOCAL TIME**, not a
  facility/PTP timecode (the mockup implied a TC source).
- **Prime / Repair** — these are legacy `/api/mxl/*` operations, not part of the `/v1` core
  contract (no `ops.*` capability). Removed from the core skin.
- **Pattern / test-generator select** — `/api/mxl/pattern` is a legacy extension, not in `/v1`.
  Removed.

Source of truth for the API: `backend/mxl-routes-v1.js` (the `/v1` contract) and
`docs/V1-CONTRACT.md`. The original full-featured mockup remains at `web/local-broadcast.html`
for reference; `web/local.html` remains the minimal reference skin.

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

## 2. What the skin drives (all `/v1`)

Every call is to the versioned core contract. Sources are addressed by their **stable opaque
id** (`src_<8hex>`), never a slot index. The UI shows a green `V1` tag on each panel; the
**Core** panel lists the live capability set from `/v1/meta`.

| UI element | ids | Wired to | Notes |
|---|---|---|---|
| Boot / discovery | — | `GET /api/mxl/v1/meta` | confirms reachability and reads `capabilities` — controls are gated on these |
| Capability list | `#capList` | `/v1/meta.capabilities` | green = advertised, grey = not available on this core |
| Sources + health | `#wall` `#busPgm` `#busPvw` | `GET /api/mxl/v1/sources` | `{id,label,kind,order,health.state}`; grids resize to `N` (wall wraps above 8) |
| State poll (1.5 s) | `#conn #connText` | `GET /api/mxl/v1/state` | `{pgm,pvw,key,busy}` by id; `connected` / `no backend` / `offline` |
| PGM monitor | `#pgm` | `<iframe src="/program/">` (mediamtx WHEP) | the program confidence feed; the core exposes `program_url` in meta |
| PVW monitor | `#pvwImg` | `GET /api/mxl/thumbs/<kind>.jpg` of the armed source | the core exposes no preview *video*; this is a thumbnail, labelled as such |
| Multiview tiles | `#wall` | `GET /api/mxl/thumbs/<kind>.jpg` (probe + `no signal` slate on 404) | click / Enter / Space arms preview |
| PREVIEW bus | `#busPvw` | `POST /api/mxl/v1/preview {source:id}` | optimistic amber until acked; refuses `health:no_signal`; gated on `control.preview` |
| **Take** | `#take` `#takeSub` | `POST /api/mxl/v1/take {}` | the single commit action — the core hard-cuts PVW→PGM; gated on `control.take` |
| PROGRAM bus (hot-punch) | `#busPgm` `#hotBtn` | `POST /api/mxl/v1/cut {source:id}` | **locked by default** (see below); gated on `control.cut` |
| DSK Key On / Off / Auto | `#keyOnBtn #keyOffBtn #keyBtn` | `POST /api/mxl/v1/key {on}` | the key is binary; whole panel hidden unless `control.key` is advertised |
| Message line | `#msg` | — | shows the `error.message` from the v1 error model (`{error:{code,message}}`) |
| Token | — | `#token=<secret>` → `localStorage.mxl_control_token` → `X-MXL-Token` on POSTs | verbatim from `local.html`; the wrapper covers `/api/mxl/` (so `/api/mxl/v1/` too) |
| OFFLINE / CONNECTED LED | `#conn` | `/v1/meta` + `/v1/state` reachability | no fake data mid-session |
| SRC n/m LED | `#srcLed` | count of sources with `health.state !== 'no_signal'` | |
| ON AIR badge | `#onair` | lit when connected **and** `pgm !== null` | derived, not a separate API |
| PGM chip | `#pgmChip` `#pgmV` `#pgmName` `#pvwV` | `/v1/state.pgm` / `.pvw` resolved to source labels | |
| Facility name | `#facName` | `<body data-facility="…">` (static) | a `facility.name` field in `/v1/meta` would be a clean follow-up |
| Local clock | `#tcHMS #tcFr` | browser wall-clock, labelled **LOCAL TIME** | explicitly **not** an MXL/PTP facility timecode |

### Hot-punch lock
A real switcher's PROGRAM row cuts instantly. A stray click on air is a worse failure than an
extra click, so the row is **locked** by default: clicking a PROGRAM key only shakes it and says
so in `#msg`. `HOT-PUNCH LOCK` (or **L**) arms the row for 20 s of idle; while armed, click or
**Shift+1–N** → `POST /v1/cut`. Remove the feature by deleting `#hotBtn` and `hotPunch` if unwanted.

### Interaction model (from `local.html`)
1. **Space / Enter** both fire **Take** (there is no separate AUTO — the core only hard-cuts).
2. `1–N` arm preview, `Shift+1–N` hot-punch, `K` toggles the key, `L` toggles the hot-punch lock.
3. Arming is optimistic (amber `pend` latch ~240 ms) then confirmed from the returned state; a
   `{error}` re-syncs from `/v1/state` instead of being swallowed.
4. Take is disabled when nothing is armed or when PVW == PGM (shows "already on air").
5. Controls disable themselves if the matching capability is absent from `/v1/meta`.

---

## 3. Demo mode (offline mock)

If `GET /api/mxl/v1/meta` at page load fails (network error, non-2xx, or `{error}`), the page
enters **demo mode** instead of an empty UI (force with `?demo=1`, disable the fallback with `?demo=0`):

* a hazard-stripe banner "DEMO MODE — simulated…", amber `DEMO MODE` LED (not green CONNECTED), a
  `SIM` tag, a `DEMO · SIM` watermark on the PGM monitor, footer says `/api/mxl/v1/* not contacted`;
* an in-page fake **core** answers the same `/v1` shapes by id (`/sources`, `/state`, `/meta`,
  `/preview`, `/take`, `/cut`, `/key`), including a dead *Guest 2* source and the structured
  `unknown_source` / `source_not_attached` error bodies;
* sources are drawn on `<canvas>` (cameras, bars, clip loop, 2×2 layout, no-signal slate), the DSK
  draws a lower-third;
* **no network requests are made** (verified: zero external hosts in either mode);
* if demo was an automatic fallback it re-checks `/v1/state` every 6 s and reloads into live mode
  once the core appears.

Controls: `?demo=1` forces demo (e.g. for design review without a backend); `?demo=0` disables the automatic fallback (recommended for an on-air deployment — set `CFG.ALLOW_DEMO_FALLBACK=false` at the top of the script to make that the default). Demo is **never** entered mid-session: if a live session loses the backend you get the red `OFFLINE`/`NO BACKEND` LED and ON AIR goes dark, not fake data.

---

## 4. How to serve it

`web/switcher.html` is served by `express.static(web)` the same way as the other skins
(`/fonts/*` resolves identically). It needs a core that answers `/api/mxl/v1/*` — i.e. the
backend with `registerV1Routes` mounted (already on by default). No routes, auth, `facility.json`
or `/program` proxy changes are required.

1. **Open it** — `http://<host>:3100/switcher.html`. It discovers the core via `/v1/meta`.
2. **Quick design review** — `http://<host>:3100/switcher.html?demo=1` (no backend needed).
3. **Make it the default skin** (optional) — point `backend/local-server.js`'s root `sendFile`
   at `switcher.html`, or `cp web/switcher.html web/local.html`. `local.html` stays as the
   minimal reference skin; `local-broadcast.html` stays as the full mockup for reference.
4. **Smoke test** — the page should show `CONNECTED`, the Core panel lit with the advertised
   capabilities, tiles with real thumbnails, PGM tally on the on-air source; arm a source
   (key `2`), press **Space**, confirm PGM tally follows.

This skin drives the **versioned** contract, so a change to the legacy `/api/mxl/*` routes does
not affect it; only a `/v1` contract change (which is versioned) would.

### Follow-ups (additive, as the core grows)
1. A `facility.name` + format field in `/v1/meta` → replace the static `data-facility` / format tags.
2. A health detail in `/v1/sources` (`last_frame_age_ms`, grain cadence) → richer tile health
   than the binary ok/no-signal, and a real answer to "green-but-frozen".
3. A low-latency preview feed (a second WHEP path advertised in meta) → replace the `#pvwImg`
   thumbnail with live PVW video.
4. If the core ever gains metering / recorder / transitions, add them here **and** to
   `/v1/meta.capabilities` — the panel will surface them automatically. Until then they stay off
   the panel (the honesty rule).

---

## 5. Accessibility & keyboard

| Key | Action |
|---|---|
| `1`–`7` (`1`–`9` max, = source count) | arm that source on **PREVIEW** |
| `Space` / `Enter` | **Take** PVW → PGM (the core hard-cuts) |
| `K` | toggle DSK key |
| `L` | arm / lock the PROGRAM-row hot-punch |
| `Shift`+`1`–`7` | hot-punch cut to that source (only while armed) |

* Space/Enter are ignored while a button has focus (so keyboard users can activate focused controls normally); after a **mouse** click the button is blurred so Space/Enter go straight back to Take. Digits, `K`, `L` always work.
* All controls are real `<button>` elements (tiles are `role=button`); bus keys expose `aria-pressed` and `aria-label="Preview: <source>"`.
* `#msg` is `role=status aria-live=polite`; the DEMO state is conveyed by text, not colour alone. Tally is redundantly encoded: colour **and** the `PGM`/`PVW` text flags on tiles and the `PGM`/`PVW` bus labels.
* `:focus-visible` amber outline; `prefers-reduced-motion` stops the on-air pulse.
* Contrast: primary text is light-on-charcoal; the dimmest engraved labels (`--engrave`, `--faint`) are decorative/secondary (not audited with a contrast tool yet).

## 6. Verification done
Rendered headless (Chromium, offline) at 1440×900; exercised against the in-page demo core for:
`/v1/meta` discovery + capability list, `/v1/sources` build, arm (key `5`) → Space take (PGM tally
followed PVW→PGM), capability-gated DSK panel, and the honest **OFFLINE** state when `/v1` is
unreachable (no fake data, greyed capability list, DSK hidden). Verified **zero** non-same-origin
requests (air-gap grep empty) and JS syntax-clean. **Not tested** against the real facility/mediamtx
(by design — no live-system access in this build).
