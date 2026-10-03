"""The facility manifest (config/facility.json) is the single source of truth for
flow UUIDs and control ports. Tools still carry a baked-in fallback map so they
run if the manifest goes missing — but those fallbacks MUST agree with the
manifest, or a deploy silently gets two different topologies depending on whether
the manifest was found. These tests pin that agreement so drift fails CI.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "facility.json"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _load_manifest():
    return json.loads(MANIFEST.read_text())


def test_manifest_is_valid_json():
    assert MANIFEST.is_file(), "config/facility.json is missing"
    _load_manifest()  # raises on malformed JSON


def test_loader_resolves_and_matches_manifest():
    import facility  # tools/ is on the path via conftest

    assert facility.domain_path() == "/mxl-domain"
    vf = facility.video_flows()
    man = _load_manifest()
    for name, entry in man["video_flows"].items():
        if isinstance(entry, dict) and "uuid" in entry:
            assert vf[name] == entry["uuid"]


def _fallback_map(py_path, var_name):
    """Extract a {name: uuid} dict literal assigned to var_name in a .py file,
    reading only the lines between the assignment and its closing brace. Avoids
    importing the module (which pulls gi/gstreamer)."""
    text = (REPO / py_path).read_text()
    start = text.index(f"{var_name} = {{")
    body = text[start : text.index("}", start)]
    out = {}
    for line in body.splitlines():
        m = re.search(r"['\"](\w+)['\"]\s*:\s*['\"]" + UUID_RE.pattern + r"['\"]", line)
        if m:
            out[m.group(1)] = UUID_RE.search(line).group(0)
    return out


def test_grain_probe_fallback_matches_manifest():
    man = _load_manifest()
    vf = {n: e["uuid"] for n, e in man["video_flows"].items() if isinstance(e, dict)}
    fb = _fallback_map("tools/grain_probe.py", "_FALLBACK_FLOWS")
    assert fb, "could not parse grain_probe _FALLBACK_FLOWS"
    for name, uuid in fb.items():
        assert vf.get(name) == uuid, f"grain_probe fallback '{name}' drifted from manifest"


def test_audio_pgm_fallback_matches_manifest():
    man = _load_manifest()
    af = {n: e["uuid"] for n, e in man["audio_flows"].items() if isinstance(e, dict)}

    # SOURCES (mixer inputs) must match the manifest
    fb = _fallback_map("tools/audio_pgm.py", "_FALLBACK_SOURCES")
    assert fb, "could not parse audio_pgm _FALLBACK_SOURCES"
    for name, uuid in fb.items():
        assert af.get(name) == uuid, f"audio_pgm source '{name}' drifted from manifest"

    # DST (PGM audio output) must match manifest 'pgm'
    text = (REPO / "tools/audio_pgm.py").read_text()
    m = re.search(r"_FALLBACK_DST\s*=\s*['\"]" + UUID_RE.pattern + r"['\"]", text)
    assert m, "could not parse audio_pgm _FALLBACK_DST"
    assert UUID_RE.search(m.group(0)).group(0) == af["pgm"], "audio_pgm DST drifted from manifest 'pgm'"


def test_control_ports_are_unique():
    man = _load_manifest()
    ports = [p["port"] for p in man["control_api"]["ports"].values()]
    assert len(ports) == len(set(ports)), "duplicate control-API port in manifest"
