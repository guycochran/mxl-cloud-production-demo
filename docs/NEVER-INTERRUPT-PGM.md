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

## ⚠️ HW REALITY (Oct 9, first build attempt) — stabilizer-per-slot is too heavy

Validated the CORE idea on HW: a stabilizer creates its STABLE flow at boot (even with no
source — "boot frame pushed"), and the selector KEEPS a wired slot pointing at an existing-
but-stale stable flow (wire → 200, slot holds while idle). So **pre-wiring to stable flows
genuinely prevents re-wire on join.** ✅ The architecture is sound.

BUT: **running a flow_stabilizer per slot ×6 overloaded the box to load 54 (on 24 cores).**
Each stabilizer is a full mxlsrc→appsink + appsrc→mxlsink pipeline (~1 core). Six of them, on
top of the switcher + encoder + thumbs, starved the box — SSH stalled, the selector wedged
(API 000), and under the starvation the stabilizers' restamp couldn't stay monotonic so the
stable flows' head FROZE (mxlsink stopped advancing) despite the push-loop reporting pushes.

**Revised approach needed (pick one):**
- **(a) Stabilize ONLY the slots that need live-join** (the roving correspondents), not all 6.
  e.g. 2–3 stabilizers, not 6. Cheapest; fits the box.
- **(b) A lighter stabilizer** — the current one fully decodes+re-encodes v210. A passthrough/
  copy stabilizer (no decode) would be far cheaper, but your notes say a plain passthrough
  stable flow stalls+timeline-jumps on respawn (the 9/12 kill). Needs the freewheel conform
  but cheaper (e.g. operate on compressed/copy, or lower the conform cost).
- **(c) GPU-assist the stabilizer conform** (the GTX 970 is now installed) — offload the
  v210↔raw there.
- **(d) Fewer, bigger correspondent slots** + accept that cutting to a NEW correspondent
  (first join) does one re-wire, but cutting BETWEEN already-joined ones is instant.

DECISION PENDING (Guy). Do NOT start 6 stabilizers again — it takes the box down.

## ✅ HW REALITY (Oct 10, option (a) build) — 2 stabilizers fit; three more findings

Guy chose **option (a)**. Built + measured on HW:

1. **Two stabilizers fit with huge headroom.** Started `mxl-stabilizer@guest5` +
   `@guest6` only. Load went **11.8 → 12.5** (vs 54 with six). Both stable flows
   (`57ab5e00`, `57ab6e00`) created cleanly, "boot frame pushed — stable flow exists
   from t0", and sat in freewheel standby (`have_input=False pushed=0 clock=True`) —
   exactly correct with no correspondent connected. PGM was undisturbed throughout
   (1.56 MB/2s). **Option (a) is viable; 2–3 stabilizers is the right ceiling for this box.**

2. **Pre-wiring the stable flows into the selector requires `/pipeline/start`, which
   RESTARTS the selector output and DROPS PGM to 0 for the republish.** This is the
   crucial nuance the architecture hinges on: the never-interrupt payoff (a correspondent
   joins → their pre-wired stable slot just starts carrying frames → NO re-wire) only holds
   *after* a one-time pre-wire done **at setup/boot**. You still pay ONE program blip to
   establish the wiring — so **do the pre-wire during bring-up, never mid-show.** Once wired,
   joins/drops are free. → The build is: teach `mxl-pgm-heal` (the boot/bring-up tool) to
   wire the **stable** correspondent UUIDs (`57abNe00`) instead of the volatile ones
   (`9eNNNe00`), so the one-time cost is folded into the boot it already does.

3. **UUIDs MUST be discovered, not taken from config (reviewer issue #2, reproduced).**
   The selector resolves flows by FULL uuid. `pattern`/`playout` have random suffixes
   (`d3e15194-6d1f-5955-8d52-52e7076b7a99`), NOT the config placeholder
   (`…-4c2a-9b3e-000000000001`). Sending the placeholder → `flow_def.json not found`.
   The pre-wire body must be built from `/pipeline/status`'s live `input_flow_uuids` +
   the domain dir listing, never from `config/facility.json` literals.

4. **`mxl-pgm-heal` has a real bug: it cuts to the first slot whose flow FILE EXISTS,
   not one that's ADVANCING.** After a re-wire it cut to slot 2 (Makito), whose flow
   existed but was stale (delta=0) → selector output froze → PGM dark. Pattern(0)/
   playout(1)/PTZ(3) were all LIVE (delta≈36). Fix: `have()` is not enough — gate the
   default cut on a 1-second head-advance check and fall back to pattern(0). (This bug
   bit us independently of the stabilizers and is the actual cause of the Oct-10 PGM-dark.)

**NEXT (option (a) build, concrete):**
- Patch `mxl-pgm-heal.sh`: (i) build inputs as pattern, playout, G1(Makito vol), G2(PTZ vol),
  then **stabilized** 57ab5e00/57ab6e00 for the two correspondent slots; (ii) replace the
  file-exists default-cut with an **advancing** check (1 s head delta > 0, else pattern).
- Make the Core cut path `active-input`-ONLY for already-wired slots (it mostly is; remove any
  residual `/pipeline/start` on join).
- Boot order: facility → stabilizers (guest5/6) → pgm-heal (wires stable, cuts to a live slot,
  republishes). Then a phone join on guest5 makes 57ab5e00 carry frames with ZERO re-wire.
- VERIFY: connect/disconnect a phone on guest5 repeatedly → PGM bytes never hit 0; cut
  guest5↔PTZ instant.

## ✅ BUILT + LIVE (Oct 10, option (a)) — never-interrupt wiring is now the boot default

`mxl-pgm-heal` was patched (on the box; see "repo note" below) and is the live boot/recovery
path. Two changes:

1. **Correspondent slots wired to STABLE flows.** Fixed sources (pattern, playout, Makito,
   PTZ, guest4) stay on volatile flows; the roving-correspondent slots guest5/guest6 wire to
   their STABLE flows `57ab5e00`/`57ab6e00` **when the stabilizer is up** (stable flow
   present), else fall back to the volatile flow so the slot is still cuttable. A new
   `add_slot()` tracks the uuid each wired slot carries in `SLOTS` so the cut logic tests the
   right flow.
2. **Default cut gated on ADVANCING, not file-exists** (the Oct-10 bug fix). New `advancing()`
   helper samples head over 1 s; the boot cut walks the slots and picks the first one actually
   moving, falling back to pattern(0). A freewheeled-dark stable flow reads "not advancing", so
   we never blank PGM by cutting to an idle correspondent slot.

**Verified on HW (Oct 10):**
- After pgm-heal: selector = `[pattern, playout, PTZ, 57ab5e00(STABLE), 57ab6e00(STABLE)]`,
  cut to playout (verified advancing), PGM live 1.5 MB/2s. ✅
- **The never-interrupt property, demonstrated:** stopped the guest5 Azure pull, pushed/killed
  test callers at the ingest, restarted the pull — through ALL that source churn the selector
  wiring stayed byte-identical and PGM never dropped (would have re-wired + blipped under the
  old design). ✅
- **Boot-durable:** re-running pgm-heal cold re-establishes stable slots [3,4] + live PGM every
  time — i.e. survives the reboot it's designed for. ✅
- NOT yet closed: a *positive* end-to-end live-video test (test pattern all the way through to
  the stable flow advancing) — blocked only by test-harness SRT encoder availability, not the
  architecture. Real proof = a phone joining via the Azure relay (previously proven), which now
  rides the stable-flow wiring. Still TODO: strip any residual `/pipeline/start` on join from
  the Core cut path (cuts should be `active-input`-only; mostly already true).

**Repo note:** the patched `mxl-pgm-heal.sh` lives on the box, NOT committed here, because it
hardcodes this box's DISCOVERED (random-suffix) pattern/playout UUIDs — box-specific config
that must not ship in the shared repo (reviewer R6e + issue #2). The proper repo version should
DISCOVER pattern/playout UUIDs at runtime (from `/pipeline/status` + the domain dir) rather than
hardcode them; that generalization is the follow-up that makes this committable.

**Box state (Oct-10, build complete):** PGM LIVE (~1.5 MB/2s, cut to a verified-advancing
source). Stabilizers guest5/guest6 ACTIVE+enabled. Selector pre-wired to stable correspondent
slots. Load ~11. guest-leg-watchdog CPU-spin guard active. All reboot-durable.
