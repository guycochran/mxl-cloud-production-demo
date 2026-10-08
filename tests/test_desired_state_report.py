# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the report-only multi-plane desired-state drift stub.

No docker, no heal, no live facility — fixture JSON only.
Covers process ≠ media ≠ dependency (alive process / dead media).
"""
import json
import subprocess
import sys
from pathlib import Path

import desired_state_report as dsr

REPO = Path(__file__).resolve().parent.parent
FACILITY = REPO / "config" / "facility.json"
FIXTURE = REPO / "tests" / "fixtures" / "actual_state_mock.json"
HEALTHY = REPO / "tests" / "fixtures" / "actual_state_healthy.json"
TOOL = REPO / "tools" / "desired_state_report.py"

TABLE_COLS = (
    "FUNCTION",
    "DESIRED",
    "PROCESS",
    "FLOW",
    "CADENCE",
    "UNIQUE_FPS",
    "UPSTREAM",
    "DOWNSTREAM",
    "DRIFT",
)


def test_fixture_and_manifest_exist():
    assert FACILITY.is_file()
    assert FIXTURE.is_file()
    assert HEALTHY.is_file()
    assert TOOL.is_file()


def test_healthy_fixture_all_ok():
    rows = dsr.build_report(FACILITY, HEALTHY)
    assert rows
    assert all(r["drift"] is False for r in rows)
    table = dsr.format_table(rows)
    for col in TABLE_COLS:
        assert col in table


def test_keyer_unique_fps_zero_is_drift_despite_running_process():
    """Classic DMF failure: process running, flow present, unique_fps=0."""
    rows = dsr.build_report(FACILITY, FIXTURE)
    by_name = {r["function"]: r for r in rows}
    assert "keyer" in by_name
    k = by_name["keyer"]
    assert k["process"] == "running"
    assert k["flow"] == "present"
    assert k["unique_fps"] == "0"
    assert k["drift"] is True


def test_audio_bypass_active_is_not_drift():
    rows = dsr.build_report(FACILITY, FIXTURE)
    by_name = {r["function"]: r for r in rows}
    assert "pgm_audio" in by_name
    assert by_name["pgm_audio"]["drift"] is False


def test_cli_prints_multiplane_table_and_exits_nonzero_on_drift():
    r = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--facility",
            str(FACILITY),
            "--actual",
            str(FIXTURE),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=20,
    )
    for col in TABLE_COLS:
        assert col in r.stdout, f"missing column {col}"
    assert "keyer" in r.stdout
    assert r.returncode == 1  # keyer unique_fps=0


def test_cli_healthy_exits_zero():
    r = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--facility",
            str(FACILITY),
            "--actual",
            str(HEALTHY),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=20,
    )
    assert r.returncode == 0
    assert "UNIQUE_FPS" in r.stdout


def test_cli_json_rows_include_planes():
    r = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--facility",
            str(FACILITY),
            "--actual",
            str(FIXTURE),
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=20,
    )
    data = json.loads(r.stdout)
    assert isinstance(data, list)
    keyer = next(row for row in data if row["function"] == "keyer")
    assert keyer["process"] == "running"
    assert keyer["unique_fps"] == "0"
    assert keyer["drift"] is True
    for field in ("flow", "cadence", "upstream", "downstream", "desired"):
        assert field in keyer
