"""guest-leg-watchdog.sh invariants (text-scanned — it's bash, no deps).

The watchdog heals the SRT-leg wedge that silently blocks phone joins: a guest ingest
chain is fanout(public) + core(video leg) + audio(audio leg), and if EITHER internal leg
is down the fanout rejects the whole incoming connection. These checks guard the safety
properties that keep the watchdog from thrashing or healing the wrong thing.
"""
import re
from pathlib import Path

WD = Path(__file__).resolve().parent.parent / "tools" / "guest-leg-watchdog.sh"


def _src():
    assert WD.is_file(), "tools/guest-leg-watchdog.sh missing"
    return WD.read_text()


def test_exists_and_bash_shebang():
    s = _src()
    assert s.startswith("#!/usr/bin/env bash") or s.startswith("#!/bin/bash")


def test_restarts_chain_in_dependency_order():
    """The cores (leg listeners) must come up before the fanout tries to write to them,
    or the fanout immediately re-wedges. Order must be audio, core, fanout."""
    s = _src()
    m = re.search(r'docker restart\s+"\$\{name\}-audio"\s+"\$\{name\}"\s+"\$\{name\}-fanout"', s)
    assert m, "heal must restart ${name}-audio ${name} ${name}-fanout in that order"


def test_has_cooldown_to_avoid_thrash():
    """A flapping chain must not be restarted every poll — a per-chain cooldown guards it."""
    s = _src()
    assert "COOLDOWN_SECONDS" in s, "no cooldown guard"
    assert "last_heal" in s, "cooldown must track last heal time per chain"


def test_skips_absent_guests():
    """guest4-6 may not exist on a smaller deployment — the watchdog must skip a guest
    whose containers aren't present rather than trying to restart nothing."""
    s = _src()
    assert "docker ps" in s and "grep -qx" in s, "must check the container exists before healing"
    assert "continue" in s, "absent guest must be skipped (continue)"


def test_heals_only_partial_chains_not_fully_down():
    """0 ports up = not provisioned / still starting (skip); 1-2 up = a leg wedged (heal);
    3 up = healthy. Healing a fully-down chain would thrash a guest that's just absent."""
    s = _src()
    assert re.search(r'"\$up"\s*-eq\s*1', s) and re.search(r'"\$up"\s*-eq\s*2', s), \
        "must heal when exactly 1 or 2 of 3 ports are up"
    assert not re.search(r'"\$up"\s*-eq\s*0.*heal', s), "must NOT heal a fully-down (absent) chain"


def test_gives_up_on_chronically_flapping_chain():
    """A leg that a restart can't fix (e.g. a video-only hardware encoder whose audio core
    cycles 'not-linked' forever) must not be restarted endlessly — give up after GIVE_UP
    heals and log, so it doesn't churn CPU. A chain going healthy resets the count."""
    s = _src()
    assert "GIVE_UP" in s, "no give-up cap on repeated heals"
    assert "GIVE UP" in s, "must log when abandoning a chronic chain"
    assert "heal_count[$name]=0" in s, "a healthy chain must reset its heal count (forgive transient wedges)"


def test_watches_all_six_guest_chains_by_default():
    """The default port map must cover guest1-6 with their real (non-contiguous) ports —
    guest3 on :8895, not the computed :8892."""
    s = _src()
    for spec in ("guest1:8890", "guest2:8891", "guest3:8895",
                 "guest4:8896", "guest5:8897", "guest6:8898"):
        assert spec in s, f"default GUEST_PORTS missing {spec}"
