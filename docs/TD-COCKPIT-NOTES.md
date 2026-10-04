# TD Cockpit — design notes (Office Hours 3.0 concept)

> **MOCKUP ONLY.** `web/td-cockpit.html` makes **no network requests**, talks to no
> switcher, hand-raise queue source, VM or third-party service, and contains no secrets. Every
> guest, hand, level, preview and layout is simulated in the page. Anything not
> wired is marked **MOCK** in the UI. Stacked on PR #18 (`design/broadcast-switcher-ui`).

View it: serve `web/` (the app already does via `express.static(web)`) and open
`/td-cockpit.html`, or just open the file locally (fonts are optional, see below).
Shots: `shots/` (1920x1080 and 1024x768 tablet, plus in-use / drag / help states).

## 1. Design rationale

**The hand queue is the hero.** In Office Hours the director's real job is
"who is next, and are they ready?" — not routing sources. Existing tools
(Quicklink Talkshow Room, TVU Partyline) have a guest queue / hot seat, but none
puts the audience hand-raise queue at the centre of the director UI
(`competitors-quicklink-tvu.md`). So the centre column is:

1. **Hand-Raise Galaxy** (frontier-research direction #4): the room as a
   constellation. Raised hands glow and pulse; queued nodes spiral outward in
   **raise-time order** (q#1 closest to the stage); the answering node sits
   beside the **STAGE** hub; held hands go amber; on-air guests carry a red ring;
   un-raised guests are dim. Curved edges with flowing particles connect each
   hand to the stage. Speaking-level halos are simulated. Click a node to select.
2. **Now answering** lane (single hot seat): avatar ring with speaking halo,
   timer, `On preview` / `Done ✓`.
3. **Up next** lane: cards in raise order with **Promote / Hold / Skip**
   (TD-only) and a big **Take next ▶**. New hands animate in; leaving hands animate out.

**Stage Map** (frontier-research direction #2): a drag-and-drop mirror of the
layout-controller boxes. Drag a guest avatar (mouse or touch) from the Room panel onto
a box, or tap a box then tap a guest. Presets 1up–8up and 16up, plus a box-count
stepper (1–16). The **Reader** gets a dedicated, reserved, assignable box
(toggle + "Move ▶" to pick which box).

**Quicklink-style guest pipeline** in the Room panel:
`waiting → queued → hot seat → on air`, with counts, connection bars, a
**program-return (RTN)** dot per guest and in the PGM tile (TVU Partyline-style
low-latency return idea), and per-guest **PFL** / **TB (talkback)** buttons.

**PVW / PGM multiviewer** tiles draw the current layouts (canvas, no video).
**CUT** and **AUTO** copy PVW → PGM. AUTO is a *hard cut only* in this mock
(consistent with the existing switcher: no transitions).

**Style:** premium dark broadcast, glass panels, restrained neon accents,
inspired by Avora's feel. *Caveat:* the saved Avora gallery files were captcha HTML stubs, not images, so
there was no direct visual reference; the style comes from general knowledge of
Avora plus the two research docs.

### Colour language
| Colour | Meaning |
|---|---|
| Violet | queued hand (q) |
| Green | answering / hot seat / on PREVIEW (a) |
| Red | on air / PROGRAM |
| Amber | held, selected, PFL |
| Cyan | talkback, reader |
| Grey | in room / waiting |

### Touch / tablet
All interactive targets are >= 44 px (checked programmatically at 1920x1080,
1440x900 and 1024x768). At <= 1180 px the Room becomes a horizontal strip on
top and the right column scrolls internally; the hero stays visible. Drag uses
pointer events (mouse + touch) and tap-to-assign is the fallback.

### Keyboard
`N` take next · `C`/`Space` CUT · `Enter` AUTO · `S` simulate raise · `A` auto-sim ·
`D` done · `H` hold · `X` skip · `R` reader box · `[` `]` box count ·
`1`–`8` looks 1up–8up, `9` = 16up · `?` help · `Esc` close.

## 2. What is MOCK (everything)

| Element | Status |
|---|---|
| Hand queue, statuses q/a/c, raise times | MOCK — in-page array; shaped like a generic hand-raise queue source (entries with status q/a/c) |
| Promote / Skip / Hold / Done / Take next | MOCK — local state only; **hold is UI-only** (no such status upstream) |
| Guests / roster / connection bars / RTN dot | MOCK — 16 fake guests + Host + Reader |
| Speaking levels / halos | MOCK — random smoothed signal |
| Source health | MOCK |
| PVW / PGM tiles | MOCK — canvas drawings of layouts, no video |
| CUT / AUTO | MOCK — copies PVW to PGM in page; AUTO = hard cut |
| Stage Map layouts | MOCK — geometry measured from on-air looks (see below); assignments are mock |
| Drag / assign / reader box | MOCK — nothing sent anywhere |
| PFL / TB / mix-minus | MOCK — toggles only |
| Session clock, wait stats, event log | MOCK |

**Layout geometry:** presets 1up–8up and 16up use geometry measured from on-air frames of the
real looks (x, y, w, h as fractions of a 16:9 canvas, accurate to about +/-0.01; see the `PRESETS` table
at the top of the script). Tiles are not all 16:9 (e.g. 3-up is three portrait columns, 5-up is a hero
plus a 2x2, 7-up is 4 over 3). Re-check against the live layout controller before relying on them.

**Fonts:** the file is self-contained (inline CSS/JS, no CDN, no external
hosts). It references the repo's already-bundled `/fonts/*.woff2` (same-origin)
with system-font fallbacks; delete the `@font-face` block for a literally
single-file build.

## 3. API each element would need (when wired)

| UI element | Needs | Notes |
|---|---|---|
| Hand queue | Hand-raise queue entries `{guestId, name, raisedAt, status: q\|a\|c}`, via a **read-only bridge** on the existing backend (server-side listener or poller -> WebSocket/SSE `hands` events to the cockpit) | Keep source credentials server-side only. Cockpit never talks to the queue source directly. |
| Promote / Complete / Skip | Bridge write endpoint, TD-auth gated (e.g. `POST /api/hands/{event}/{id}/status`), writing `a` / `c` | Skip needs a definition (see open questions). |
| Hold | New status or a bridge-side overlay flag | Not in q/a/c today. |
| Raise/lower from guest side | Already happens upstream in the queue source; cockpit only observes | Animated entry driven by new keys appearing. |
| Speaking levels | Per-participant audio level stream (~10 Hz) from the media layer (LiveKit/room server or mixer) -> bridge WebSocket `levels {guestId: 0..1}` | Drives halos on galaxy, lane and roster. |
| Source health | Per-guest `{rttMs, packetLoss, bitrate, state}` from the media layer | Drives connection bars; could auto-flag "not ready". |
| Previews | Low-res per-guest thumbnails (1–2 fps JPEG) or WHEP; plus PGM confidence feed | Tiles currently draw layouts only. |
| Guest pipeline state | waiting/queued/hot seat/on air derived from hand status + current PVW/PGM assignment | Derivable client-side once the above exist. |
| Stage Map | Read/write of layout box -> input assignment, box count, and saved looks (`GET/PUT /api/stage/layout`) | Geometry now measured from on-air looks; confirm against the live layout controller. |
| CUT | Existing `POST /api/mxl/take` (hard cut) | AUTO maps to the same hard cut unless transitions are added. |
| Program return | Mix-minus / return-feed status per guest from the media layer | Indicator only for now. |
| PFL / talkback / IFB | Audio routing API (PFL bus, talkback to selected guest, mix-minus) | Biggest unknown; probably a separate audio path. |
| TD-only controls | Existing panel auth/role check | Cockpit should refuse writes for non-TD sessions. |

## 4. Open questions for Guy

1. **How many boxes?** Is the layout controller's box limit 4 (typical), or are the 8up / 16up
   looks multi-layer or multi-ME? Do we cap the stepper at the real limit?
2. **Which layouts?** Confirm 1up–8up + 16up are the right set and send (or let me
   extract) the real geometry; are there named looks beyond Nup (e.g. side-by-side, PiP)?
3. **Reader box:** is the Reader a fixed box in every look (and which one), or
   floating? Should it be locked from guest drops, and should it auto-move per look?
4. **Hold semantics:** hold = keep position but skip over; or send to back; should
   it be visible to the guest?
5. **Skip semantics:** back of the queue, or mark completed without answering?
6. **Single vs multiple answering:** one hot seat at a time, or allow two on stage?
7. **Auto-place on promote:** should promoting a hand automatically drop that guest
   into the next free box on PREVIEW (currently an option, default on)?
8. **Previews:** thumbnails vs WHEP; how much bandwidth is acceptable on the VM?
9. **Talkback / PFL path:** what exists today (phone bridge, IFB, Zoom/other)?
10. **Who may use TD controls** (just the TD, or also a producer view read-only)?
11. **AUTO:** keep it as hard cut forever, or reserve for a future mix/fade?
