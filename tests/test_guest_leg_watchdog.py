"""guest-leg-watchdog.sh invariants (text-scanned — it's bash, no deps).

HARD LESSON baked into these tests (HW Oct 9): restarting a guest chain recreates its MXL
flows → the input-selector re-wires → if that happens repeatedly the selector churns and
the whole facility destabilizes (PGM sticks, cuts fail, thumbnails flap). The first
watchdog restarted whole chains on any leg blip and broke the facility. So the watchdog is
now deliberately MINIMAL: it only watches the PUBLIC listener, never touches a chain with
live video, restarts ONLY the fanout (not the cores), and is OFF by default.
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


def test_off_by_default():
    """It's a sharp tool — must do nothing unless deliberately enabled (ENABLE=1)."""
    s = _src()
    assert re.search(r'ENABLE=\$\{ENABLE:-0\}', s), "watchdog must default ENABLE=0 (observe-only)"
    assert 'ENABLE" = "1"' in s or "ENABLE\" == \"1\"" in s, "heal must be gated on ENABLE=1"


def test_never_touches_a_chain_with_live_video():
    """The bug that broke the facility: restarting a chain whose video is live churns the
    selector. The watchdog MUST skip a chain whose video flow is advancing."""
    s = _src()
    assert "video_advancing" in s, "no live-video guard"
    # the guard must cause a skip/continue, not a heal
    assert re.search(r'video_advancing "\$vuuid".*;\s*then\s*strike\[\$name\]=0;\s*continue', s, re.S), \
        "a chain with advancing video must be skipped, never healed"


def test_restarts_only_the_fanout_not_the_cores():
    """Restarting the cores recreates the MXL flows → selector churn. Heal must restart ONLY
    the public-listener fanout, leaving the cores (and their flows) untouched."""
    s = _src()
    assert re.search(r'docker restart "\$\{name\}-fanout"', s), "heal must restart the fanout"
    assert not re.search(r'docker restart "\$\{name\}-audio"\s+"\$\{name\}"', s), \
        "heal must NOT restart the cores (that recreates flows and churns the selector)"


def test_only_acts_on_a_dead_public_listener():
    """The only thing that silently blocks a join is the PUBLIC listener being down on an
    idle slot. Internal leg flaps are explicitly NOT the watchdog's concern anymore."""
    s = _src()
    assert re.search(r'if bound "\$pub"; then', s), "must key off the public listener being up"


def test_has_strikes_cooldown_and_giveup():
    s = _src()
    assert "STRIKES" in s and "COOLDOWN_SECONDS" in s and "GIVE_UP" in s, \
        "must have strikes + cooldown + give-up guards against thrash"


def test_watches_all_six_guest_chains_with_real_ports():
    """Default map covers guest1-6 on their REAL (non-contiguous) public ports + video UUIDs."""
    s = _src()
    for spec in ("guest1:8890", "guest2:8891", "guest3:8895",
                 "guest4:8896", "guest5:8897", "guest6:8898"):
        assert spec in s, f"default GUEST_PORTS missing {spec}"
    assert "9e333e00" in s, "guest3 video UUID must be in the map (for the live-video check)"
