# Never-interrupt-PGM: the live correspondent architecture

**The requirement (Guy, Oct 9, trade-show use case):** correspondents roam a show floor,
each scans a QR → publishes from their phone → the director cuts them LIVE. **Cutting to a
correspondent — or a correspondent joining/dropping — must NEVER interrupt the program.**

## Why it breaks today

Adding a guest to the live switcher re-wires the input-selector (`POST /pipeline/start`
with a new input list). That restarts the selector's router thread; the encoder reading the
selector output sees a grain gap and stalls → **PGM freezes ~15s** until `mxl-pgm-heal`
republishes. Worse, the selector **drops** any slot whose flow isn't currently advancing, so
an idle (not-yet-connected) correspondent's slot can't just sit pre-wired — it falls out.

Two root causes, each with an existing-but-unassembled fix in `tools/`:

1. **Re-wire on join** → restart → PGM gap.
2. **Idle slots collapse** → can't pre-wire → forced to re-wire when a correspondent appears.

## The architecture (assemble + extend what exists)

### 1. A persistent STABLE flow per correspondent slot — `tools/flow_stabilizer.py`
The stabilizer reads a VOLATILE guest flow and writes a STABLE flow that is **created once at
boot and never recreated**. When the volatile side goes silent (no correspondent, or a
reconnect recreates the volatile flow), it swaps ONLY its reader in-place — the stable flow
and its grains keep flowing (freewheel). Downstream consumers (the selector slot) point at the
STABLE flow and therefore **never wedge and never disappear.**

→ Run one stabilizer per correspondent slot: guest1..guestN →
`flow_stabilizer.py guestK <guestK-uuid> <stabilized_guestK-uuid> "Guest K Stable"`.
Today only `stabilized_guest1/2` exist in `config/facility.json`; **add stabilized_guest3..6**
(uuids `57ab3e00`..`57ab6e00`), one per slot we want live-joinable.

### 2. Pre-wire ALL slots to their STABLE flows at boot — fixed-length slot map
Because the stable flows always exist, the selector can be wired ONCE at startup with the full
fixed slot list (cam, playout, pattern, cam2, stabilized_guest1..6, layout) and **never
re-wired again.** `tools/guest_slot_watcher.py` already implements the fixed-length slot map
with a SAFE placeholder — but with stabilizers present, the placeholder is rarely needed
because the stable flow is the permanent wiring. A correspondent joining just makes their
stable flow carry real frames; the slot was already wired → **cutting to them is instant and
touches nothing upstream of their own slot.**

### 3. Contribution path per correspondent — Azure SRT relay (IP-hidden)
Each correspondent's phone publishes to the Azure SRT relay (see
[[mxl-azure-srt-relay-2026-10-09]] / `MXL_GUEST_HOSTS`), the box pulls outbound → feeds that
guest's volatile ingest → stabilizer → pre-wired slot. Home IP never exposed. One relay
port-pair per simultaneous correspondent (guest5 = 8897/18897 today; add 8896/18896,
8898/18898, … for more).

## Result
```
Correspondent phone ─SRT→ Azure relay ─(box pulls)→ guestK volatile ─→ stabilizer ─→ stabilized_guestK
                                                                                         │ (pre-wired at boot)
                                                              input-selector (NEVER re-wired) ──→ encoder ──→ PGM
```
- Correspondent joins/drops/reconnects → only their volatile flow changes; stabilizer absorbs
  it; selector + encoder + PGM untouched.
- Director cuts to any slot instantly (`active-input` only — no `/pipeline/start`).
- **PGM is never interrupted.**

## Build plan (do on a CLEAN facility bring-up, not patched onto today's hand-surgered box)
1. Add `stabilized_guest3..6` to `config/facility.json` (box copy; uuids 57ab3e00..57ab6e00).
2. `quickstart.sh`/`bring-up`: launch a `flow_stabilizer.py` per guest slot (systemd:
   `mxl-stabilizer@guestK` template unit).
3. Wire the selector ONCE at boot to the fixed list of STABLE guest flows (+ cam/playout/
   pattern/layout). Remove the per-join `/pipeline/start` re-wire from the Core/pgm-heal cut
   path — cuts become `active-input`-only.
4. Per correspondent: an Azure relay port-pair + a box puller (template:
   `mxl-guestK-azure-pull`), advertised via `MXL_GUEST_HOSTS=guestK=<azure-ip>`.
5. Verify: with PGM live on cam, connect/disconnect a phone on guest5 repeatedly → PGM bytes
   never drop to 0; cutting guest5↔cam is instant.

## Known-good invariants to preserve (learned the hard way Oct 9)
- Restarting guest CORES or re-wiring the selector ripples into PGM — the whole point of this
  architecture is to make both unnecessary during a live show.
- The gst-keyer (lower-third) must read a STABLE program flow too, or it stalls on re-wire.
- `mxl-pgm-heal` stays as the BOOT bring-up + recovery tool, not a per-join mechanism.
