# Turnkey Companion setup (Windows PC + Stream Deck)

Two ways to drive the MXL switcher from a Stream Deck. Pick by whether
you want tally.

## Option A — One-click page import (cuts/take/layout/key/warm-up, NO tally)
Zero module install; uses Companion's built-in Generic HTTP module.

1. Install **Bitfocus Companion** (companion 3.x) on the Windows PC.
2. Web UI → **Import/Export** → **Import** → choose
   `mxl-switcher-page.companionconfig` (in this folder) → import as a page.
3. Done. The page has, laid out Stream-Deck-XL style:
   - Row 1: PGM hot-cut — Cam 1 / Cam 2 / Playout / Pattern / Guest 1 / Guest 2 / Layout
   - Row 2: PVW arm — same seven (arms the server preview bus)
   - Row 3: TAKE · KEY ON · KEY OFF · WARM-UP · 2-UP · 4-UP · PiP
4. Quit the Elgato Stream Deck app (tray → Quit) so Companion sees the deck,
   then Surfaces → assign the deck to the page.

Base URL is baked in (`https://prodbots.com`). Buttons fire cuts at the
switcher's native ~25 ms.

## Option B — Full module with RED/GREEN TALLY (recommended for the show)
Companion loads our custom module natively (Buttons 1.7 does not).

1. Install Companion 3.x + Node.js LTS.
2. `cd companion-module-mxl-switcher && npm install`
3. Companion → Settings → **Developer modules path** → the repo root
   (parent of this folder). Companion picks it up.
4. Connections → add **mxl-switcher** (base URL prefilled).
5. Buttons tab → Presets → mxl-switcher → drag the ready-made
   PGM/PVW/TAKE/KEY/WARM-UP presets. Tally is live (1 s status poll):
   on-air source burns red, armed source green, feedless dims.

## Note on Bitfocus Buttons (the enterprise product)
Buttons 1.7's import wants a full ZIP backup, not a .companionconfig, and
it only loads signed/downloaded modules — so on Buttons use the NMOS
router view for the standards story and Companion for the panel. See
../docs/FIELD-NOTES.md §4.
