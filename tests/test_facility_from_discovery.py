"""R2 fixture test: the quickstart's discovered flow UUIDs must flow into a manifest
the UI/backend can use, so cuts resolve to REAL flows on a fresh box — not the lab's
fixed UUIDs that don't exist there.

Feeds a recorded `mxl-info -l` sample (tests/fixtures/mxl-info-quickstart.txt, whose
UUIDs are deliberately DIFFERENT from config/facility.json) through the emitter and
asserts the result is a valid manifest keyed on the discovered UUIDs.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EMITTER = REPO / "tools" / "facility_from_discovery.py"
FIXTURE = REPO / "tests" / "fixtures" / "mxl-info-quickstart.txt"


def _run(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(EMITTER), *args],
        input=stdin, capture_output=True, text=True,
    )


def test_emitter_and_fixture_exist():
    assert EMITTER.is_file()
    assert FIXTURE.is_file()


def test_generates_manifest_from_discovered_uuids():
    r = _run("--from", str(FIXTURE), "--vm", "127.0.0.1")
    assert r.returncode == 0, r.stderr
    m = json.loads(r.stdout)
    vf = m["video_flows"]
    # the TG's "Pattern Video" -> role pattern; the clip's "Clip Video" -> role playout
    assert vf["pattern"]["uuid"] == "aa11bb22-0000-4000-8000-000000000001"
    assert vf["playout"]["uuid"] == "bb22cc33-0000-4000-8000-000000000001"
    assert vf["selector"]["uuid"] == "cc33dd44-0000-4000-8000-000000000001"
    assert vf["keyer"]["uuid"] == "dd44ee55-0000-4000-8000-000000000001"


def test_discovered_uuids_differ_from_repo_manifest():
    """The whole point of R2: a fresh box's UUIDs are NOT the repo's fixed ones."""
    repo = json.loads((REPO / "config" / "facility.json").read_text())
    gen = json.loads(_run("--from", str(FIXTURE)).stdout)
    assert gen["video_flows"]["pattern"]["uuid"] != repo["video_flows"]["pattern"]["uuid"]


def test_layout_shows_only_existing_sources():
    """Only slots whose writers ran appear — a stranger with just TG+clip sees 2 tiles,
    not 7 (so no 'source not attached' for cam/guest slots that don't exist)."""
    m = json.loads(_run("--from", str(FIXTURE)).stdout)
    assert m["program"]["layout_inputs"] == ["playout", "pattern"]
    assert m["program"]["layout_input_labels"] == ["Playout", "Pattern"]
    # selector/keyer are plumbing, NOT selectable source tiles
    assert "selector" not in m["program"]["layout_inputs"]
    assert "keyer" not in m["program"]["layout_inputs"]


def test_generated_manifest_loads_via_facility_js_and_resolves_cuts(tmp_path):
    """End-to-end: the generated manifest, loaded through facility.js the way
    local-server does, resolves a cut target to the DISCOVERED uuid (not -1)."""
    gen = _run("--from", str(FIXTURE)).stdout
    out = tmp_path / "facility.generated.json"
    out.write_text(gen)
    # drive facility.js with MXL_FACILITY_JSON, assert pattern role -> discovered uuid
    node = subprocess.run(
        ["node", "-e",
         "const f=require('./backend/facility');"
         "const fac=f.load();"
         "process.stdout.write(JSON.stringify({"
         "  path: fac._path,"
         "  pattern: f.videoFlow('pattern'),"
         "  layout: fac.program.layout_inputs,"
         "}));"],
        cwd=str(REPO), env={"MXL_FACILITY_JSON": str(out), "PATH": __import__("os").environ["PATH"]},
        capture_output=True, text=True,
    )
    assert node.returncode == 0, node.stderr
    res = json.loads(node.stdout)
    assert res["path"] == str(out)
    assert res["pattern"] == "aa11bb22-0000-4000-8000-000000000001", "cut target must be the discovered uuid"
    assert res["layout"] == ["playout", "pattern"]


def test_empty_input_fails_cleanly():
    r = _run(stdin="")
    assert r.returncode != 0
    assert "no flows" in r.stderr.lower()


def test_no_known_labels_fails_cleanly():
    r = _run(stdin="\tVideo : 12345678-0000-4000-8000-000000000001 - Totally Unknown Label\n")
    assert r.returncode != 0
    assert "no known source labels" in r.stderr.lower()
