# companion-module-mxl-switcher

Bitfocus Companion module for the MXL cloud switcher in this repo:
Stream Deck cuts at the switcher's native 20-30 ms, SuperSource layout
buttons, keyer toggle, the warm-up sweep — and **real program tally**
(buttons burn red for the on-air source, dim for feedless sources) via
a 1 s status poll.

## Install (Companion 3.x developer module)
1. `cd companion-module-mxl-switcher && npm install`
2. Companion → Settings → Developer → set the *developer modules path*
   to this repo's directory (the parent of this folder).
3. Add a connection: search "mxl-switcher"; set the base URL
   (default `https://prodbots.com`).
4. Drag the ready-made presets (Cuts / Switcher categories) onto keys.

## Status
Field-tested against the live facility API. Actions: cut, keyer
on/off/toggle, warm-up, SuperSource style+boxes. Feedbacks: PGM tally,
no-feed dim, keyer state. Variables: `pgm_label`, `pgm_slot`,
`key_state`.

The standards destination for panel control is AMWA **BCP-007-03**
(NMOS Support for MXL) — see `../docs/FIELD-NOTES.md` §4. This module
is the pragmatic bridge until that lane lands.
