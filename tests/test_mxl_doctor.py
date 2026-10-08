# SPDX-License-Identifier: Apache-2.0
"""mxl-doctor is the single front door that consolidates the four health scripts.
These check the dispatcher's SAFE, environment-free paths — help text, argument
handling, and that every script it dispatches to actually exists. The report and
heal paths need docker/containers/the live facility, so they're not exercised here.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCTOR = REPO / "scripts" / "mxl-doctor"


def _run(*args):
    return subprocess.run(
        ["bash", str(DOCTOR), *args],
        capture_output=True, text=True, cwd=str(REPO), timeout=20,
    )


def test_mxl_doctor_exists_and_executable():
    assert DOCTOR.is_file(), "scripts/mxl-doctor missing"
    import os
    assert os.access(DOCTOR, os.X_OK), "scripts/mxl-doctor is not executable"


def test_syntax_is_valid():
    r = subprocess.run(["bash", "-n", str(DOCTOR)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_help_lists_all_four_capabilities():
    r = _run("help")
    out = r.stdout + r.stderr
    for token in ("check", "--deep", "--watch", "heal"):
        assert token in out, f"help text missing '{token}'"


def test_unknown_command_fails_cleanly():
    r = _run("bogus")
    assert r.returncode == 2
    assert "unknown command" in (r.stdout + r.stderr)


def test_heal_requires_a_known_leg():
    assert _run("heal").returncode == 2            # no leg -> usage, exit 2
    assert _run("heal", "bogus").returncode == 2   # bad leg -> error, exit 2


def test_dispatch_targets_exist():
    """Every script mxl-doctor can hand off to must be present, or a subcommand
    would fail at the worst time (a wedge, mid-show)."""
    for rel in ("scripts/doctor.sh", "tools/selector-doctor.sh",
                "tools/program-fps-doctor.sh", "tools/guest-leg-doctor.sh"):
        assert (REPO / rel).is_file(), f"mxl-doctor dispatch target {rel} missing"
