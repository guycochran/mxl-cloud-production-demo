"""Stable guest-slot semantics for guest_slot_watcher.

The bug this guards: when guests were appended only if present, an absent Guest 1 made
Guest 2 land on slot 2; when Guest 1 later connected, Guest 2 jumped to slot 3 — moving
an operator's / Companion's button under them. The fix is a FIXED-LENGTH slot list where
an absent guest's slot is held by a safe placeholder, so indices never move.
"""
import importlib
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _watcher():
    # import fresh so default env (BASE/GUEST labels) applies
    import guest_slot_watcher as w
    return importlib.reload(w)


PATTERN = "p" * 36
CLIP = "c" * 36
G1 = "1" * 36
G2 = "2" * 36


def test_absent_guest_is_padded_not_collapsed():
    w = _watcher()
    # only Pattern, Clip, Guest 2 present — Guest 1 NOT streaming
    fm = {"Pattern Video": PATTERN, "Clip Video": CLIP, "Guest 2": G2}
    uuids, present = w.selector_inputs(fm)
    # 4 slots: Pattern, Clip, Guest1(padded->Pattern), Guest2
    assert uuids == [PATTERN, CLIP, PATTERN, G2]
    assert present == ["Guest 2"]


def test_guest2_slot_is_stable_when_guest1_appears():
    w = _watcher()
    before = {"Pattern Video": PATTERN, "Clip Video": CLIP, "Guest 2": G2}
    after = {"Pattern Video": PATTERN, "Clip Video": CLIP, "Guest 1": G1, "Guest 2": G2}
    u_before, _ = w.selector_inputs(before)
    u_after, _ = w.selector_inputs(after)
    # Guest 2 is slot index 3 in BOTH cases — it never moves.
    assert u_before[3] == G2
    assert u_after[3] == G2
    # slot 2 goes from padded-Pattern to the real Guest 1
    assert u_before[2] == PATTERN
    assert u_after[2] == G1


def test_list_length_is_fixed_regardless_of_guests():
    w = _watcher()
    none_up = {"Pattern Video": PATTERN, "Clip Video": CLIP}
    all_up = {"Pattern Video": PATTERN, "Clip Video": CLIP, "Guest 1": G1, "Guest 2": G2}
    assert len(w.selector_inputs(none_up)[0]) == 4
    assert len(w.selector_inputs(all_up)[0]) == 4


def test_returns_none_until_safe_flow_present():
    w = _watcher()
    # no Pattern (the default safe label) yet -> not ready, don't touch selector
    uuids, _ = w.selector_inputs({"Clip Video": CLIP})
    assert uuids is None


def test_slot_map_marks_idle_guests():
    w = _watcher()
    fm = {"Pattern Video": PATTERN, "Clip Video": CLIP, "Guest 2": G2}
    # build the map the same way write_slot_map does, without touching the filesystem
    m = {str(i): (lbl if lbl in fm else f"{lbl} (idle)")
         for i, lbl in enumerate(w.SLOT_LABELS)}
    assert m["0"] == "Pattern Video"
    assert m["2"] == "Guest 1 (idle)"
    assert m["3"] == "Guest 2"


# ── flow_stabilizer fold: guest slots prefer the STABLE flow when present ──────
G1_STABLE = "s" * 36


def test_prefers_stable_flow_over_volatile():
    w = _watcher()
    # both the volatile "Guest 1" and its "Guest 1 Stable" are present
    fm = {"Pattern Video": PATTERN, "Clip Video": CLIP,
          "Guest 1": G1, "Guest 1 Stable": G1_STABLE}
    uuids, present = w.selector_inputs(fm)
    assert uuids[2] == G1_STABLE, "slot should point at the stable flow, not volatile"
    assert present == ["Guest 1 (stable)"]


def test_reconnect_with_stable_present_needs_no_reattach():
    """THE fold invariant: once a guest's stable flow exists, its raw flow coming
    and going (a reconnect) must NOT change the uuid list — so the watcher issues
    no selector re-attach and never reverts the active cut."""
    w = _watcher()
    connected = {"Pattern Video": PATTERN, "Clip Video": CLIP,
                 "Guest 1": G1, "Guest 1 Stable": G1_STABLE}
    reconnecting = {"Pattern Video": PATTERN, "Clip Video": CLIP,
                    "Guest 1 Stable": G1_STABLE}  # raw dropped mid-reconnect, stable persists
    assert w.selector_inputs(connected)[0] == w.selector_inputs(reconnecting)[0]


def test_falls_back_to_volatile_without_stabilizer():
    w = _watcher()
    fm = {"Pattern Video": PATTERN, "Clip Video": CLIP, "Guest 1": G1}
    assert w.selector_inputs(fm)[0][2] == G1  # no stable flow -> volatile, unchanged


def test_prefer_stable_can_be_disabled():
    import os
    os.environ["PREFER_STABLE"] = "0"
    try:
        w = _watcher()
        fm = {"Pattern Video": PATTERN, "Clip Video": CLIP,
              "Guest 1": G1, "Guest 1 Stable": G1_STABLE}
        assert w.selector_inputs(fm)[0][2] == G1  # stable ignored when disabled
    finally:
        del os.environ["PREFER_STABLE"]
        _watcher()  # restore default-env module for any later test
