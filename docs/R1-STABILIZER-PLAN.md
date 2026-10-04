# R1 — eliminate the cold-reader wedge with stabilizers (design + PoC)

**Status: mechanism PROVEN on hardware (Oct 4 2026); full selector integration is
the remaining work.** This documents the correct fix for review item R1 and the
proof-of-concept that validates it, so the implementation can be finished in a
focused VM session rather than re-derived.

## The problem (R1, confirmed earlier)
A cut can "stick on the previous source": the selector's `active_input` moves, the
UI tally moves, thumbnails move — but the program VIDEO stays on the old source.
Root cause: the input-selector keeps one reader per input, and a reader whose flow
was **recreated** (guest reconnect, ingest restart) wedges permanently
(MXL v1.1.0 behaviour). The per-cut "pre-warm" shipped earlier is a band-aid — it
adds a ~250ms stale window and can't recover a *currently* stuck source.

## The right fix: stabilizers (not pre-warm)
`tools/flow_stabilizer.py` already implements the first-principles cure. It reads a
VOLATILE source flow and writes a STABLE flow that is created once and **never
recreated**; when the volatile side is recreated it swaps ONLY its reader element
in-place (sink + stable flow keep running). The selector points at the STABLE flow,
so its reader never sees a recreation and **can never wedge**. This is the same
architecture the live facility's guest slots use (`stabilized_guest1/2` roles).

With sources behind stabilizers, the per-cut pre-warm becomes unnecessary and can
be removed — readers are always hot.

## PoC (verified on VM1, Oct 4)
Ran cam2 (volatile `ca222e00`) through a stabilizer into a stable flow
(`57ab2e00`), wired the selector to `[cam1, stable-cam2]`, then recreated cam2's
volatile flow (the exact wedge trigger). The stabilizer log:

```
flow_stabilizer cam2stab (freewheel): ca222e00 -> 57ab2e00 running
boot frame pushed — stable flow exists from t0
reader swapped (sink untouched)        <- the volatile flow was recreated; the
                                           stabilizer swapped its reader in-place
diag out pushed=5698 have_input=True clock=True   <- stable flow still live after
```

The stable flow stayed live across the recreation — the mechanism works.

## Remaining integration work (needs a VM session)
The PoC proved the stabilizer survives recreation, but a clean end-to-end needs:
1. A stabilizer **per cut-source** (cam/cam2/guests), each writing a `stabilized_*`
   flow (manifest roles already exist for guests; add them for cam/cam2/pattern).
2. The **selector wired to the stable flows**, not the volatile ones — so the
   quickstart/healer selector-start uses the stabilized UUIDs.
3. **Remove the per-cut pre-warm** from `mxl-routes.js` once (2) lands (it's then
   dead weight; keep `MXL_PREWARM` as an escape hatch for the no-stabilizer case).
4. Observed open detail: in the PoC, after cutting to the stable slot the program
   briefly still showed the previous source and a direct read of the stable flow
   contended — needs a careful look at the keyer's read of the selector output
   when the selector is re-pointed at a freshly-created stable flow (likely the
   selector-output-reader needs the same stabilizer treatment, or a restamp
   alignment). This is the piece to finish before claiming the wedge is GONE.

## Why this isn't shipped yet
Wiring half of this (stabilizers without the selector/keyer alignment in #4) would
be worse than the current pre-warm — it adds CPU (a stabilizer per source) without
fully closing the wedge. Finishing it right is a focused VM session: stand up the
full stabilizer chain, confirm A→B→A across a guest reconnect shows zero wedge via
the keyer-PGM frame grab, THEN remove the pre-warm. Tracked as the R1 follow-up.
