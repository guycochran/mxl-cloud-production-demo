<!-- SPDX-License-Identifier: Apache-2.0 -->
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

## UPDATE (Oct 4, 2nd VM session) — the §4 detail is a KNOWN hard problem; approach revised

Stood up the full chain (cam1+cam2 each through a stabilizer → selector on the two
stable flows). Result: the selector errored **`read source grain N: Out of range -
too early`** and the program rendered blank. Root cause identified:

- The stabilizer restamps its output to `now + MARGIN` (free-running clock). The
  **selector needs the offset-lock-FOLLOWING-the-buffer-timeline restamp** instead
  (the cam_ingest / layout_pgm-v3 model, `RESYNC_NS=150ms` re-lock). This exact
  "grain too early" failure + its fix is documented in MXL-NOTES (slot-6 layout
  saga): free-running restamp → unbounded ring drift → readers read past the write
  index. The stabilizer uses the WRONG restamp model for a selector consumer.
- Separately, MXL-NOTES flags that a prior stabilizer-per-source deployment once
  **wedged VM1** (D-state I/O pileup, load 163/32, ~60 zombie python procs from an
  unreaped supervisor, spawn storm). So a stabilizer PER cut-source carries real
  operational risk at scale, not just CPU.

### Revised recommendation (cheaper AND safer than a separate stabilizer)
The ingest (`contribution_core` / cam_ingest / guest_ingest) **already** restamps
correctly onto the local clock — that's why cutting to volatile ingest flows works.
The only reason the wedge exists is that the ingest **recreates its flow** on
reconnect. So the right fix is to fold the stabilizer's "create-once-never-recreate
+ swap-reader-in-place" behaviour INTO the ingest pipeline, reusing the ingest's
ALREADY-CORRECT restamp — rather than bolting a separate stabilizer (wrong restamp,
extra process, zombie/spawn risk) after it. One flow, correct timeline, no wedge,
no extra process.

That's a change to `contribution_core` (make the mxlsink flow persistent across
reconnects + swap the source leg in-place), which is a focused piece of work with a
clear design — but bigger than a config tweak, and it must not regress the
HW-proven A/V lip-sync path. Until it's done and HW-verified (A→B→A across a
reconnect, zero wedge via the keyer-PGM grab), the current **per-cut pre-warm stays
as the safe interim** — it's imperfect (250ms window, can't recover a stuck current
source) but it doesn't risk a spawn-storm wedge.

## Why this isn't shipped yet
Wiring stabilizers naively (the first idea) is WORSE than the pre-warm: wrong
restamp → blank program, plus the spawn-storm risk. The correct fix (persistent
flow inside the ingest) is the right next step but is real work on the lip-sync-
critical ingest path — do it in a dedicated session with the A/V regression in
mind, THEN remove the pre-warm. Tracked as the R1 follow-up.

## UPDATE (Oct 5 2026) — the persistent-flow fix is BUILT + HW-VERIFIED

Implemented in `tools/contribution_core.py` as an **opt-in** mode
(`MXL_INGEST_PERSISTENT=1`, or `adapter.persistent_flow=True`; env wins). Default
stays the supervisor-restart path, so the HW-proven A/V lip-sync behaviour is
untouched until a deployment opts in.

**How it works.** The pipeline is split at a named `jbuf` queue:

    [ source leg ]  →  jbuf  →  [ conform ]  →  mxlsink   (the TAIL is persistent)

The mxlsink (and its flow UUID) is created ONCE. On a source EOS/error the bus
callback rebuilds ONLY the source leg: unlink it from `jbuf` (by jbuf's own sink-pad
peer — removing the element does not reliably free the peer pad), set NULL, remove,
re-parse the adapter fragment, relink (element-level `link`, which resolves the
ghost pad — a manual ghost→ghost `pad.link` returns `wrong-hierarchy`), and
`sync_state_with_parent`. A fatal error from the TAIL (sink/conform) still exits for
a supervisor restart; only source-leg faults are recovered in place (`_from_tail`
routes them).

**Monotonic re-lock (the piece §4 flagged).** A reconnected source restarts its PTS
near 0 (new RTP/SRT/encoder session). With the offset still locked from the first
leg, the new frames would map BACKWARD and the flow's grain index would rewind —
which is exactly the selector's `read source grain … too early`. So the rebuild
clears the lock and the probe re-locks on the new leg's first frame, CLAMPED to
`last_mapped + 1 grain` so the flow only ever moves forward. Same clamp for audio
(`next_pts`). This reuses the ingest's already-correct restamp — no separate
stabilizer, no second process, no spawn-storm risk.

**HW proof (VM1, Oct 5).** A `videotestsrc` adapter that EOSes every N frames drove
the exact wedge trigger. In persistent mode across **10 reconnects over 5 legs**:
the flow dir kept the SAME `inode` and `ctime` throughout (`inode=92`, created once,
never recreated); `cadence offset locked` fired ONCE and the frame counter ran
continuously `n=60…540` with small `err` (−10..−58 ms); zero wedge. A selector wired
to `[stable-TG, persistent-test-flow]` cut to the test flow and the **program video
followed the cut** (selector-output grab = the test pattern), and kept following
after reconnects. The default (non-persistent) path was re-run as a regression:
`persistent=False`, clean `offset locked: 125ms`, byte-identical restamp, EOS→exit —
unchanged.

**Residual, documented, orthogonal to R1.** A freshly-restamped ingest flow sits
~2 grains (MARGIN) ahead of the domain read head, so a selector that catches the
write head logs a TRANSIENT `too early` until it settles — the program still renders
the source. This is NOT introduced by the persistence change: the **stock
non-persistent ingest shows the same** `too early`/`Unknown error: 11` on the first
selector cut. It's a MARGIN-vs-selector-consumer tuning question, tracked separately.

**Follow-up (not done here, deliberately):** once a deployment runs persistent mode
in the live facility, the per-cut pre-warm in `mxl-routes.js` becomes dead weight and
can be removed (keep `MXL_PREWARM` as the no-persistence escape hatch). Left in place
for now so this change is purely additive.

Tests: `tests/test_contribution_core.py` (8 new — opt-in resolution, tail==one-shot
conform/sink, init builds tail-then-leg, monotonic-relock-never-rewinds), stubbed-gi
so they run in CI. All 25 contribution tests + full python suite + node suite green.
