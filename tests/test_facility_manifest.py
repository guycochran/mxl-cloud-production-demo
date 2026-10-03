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


def test_backend_js_fallbacks_match_manifest():
    """The backend's mxl-routes.js carries baked-in UUID fallbacks (used when the
    manifest can't be found). CI is Python-only, so scan the JS as text and pin
    each fallback literal to the manifest — same drift-guard as the Python tools."""
    man = _load_manifest()
    vf = {n: e["uuid"] for n, e in man["video_flows"].items() if isinstance(e, dict)}
    af = {n: e["uuid"] for n, e in man["audio_flows"].items() if isinstance(e, dict)}
    js = (REPO / "backend" / "mxl-routes.js").read_text()

    # each `_vf('role', 'uuid')` / `_af('role', 'uuid')` fallback must match
    for fn, table in ((r"_vf", vf), (r"_af", af)):
        for m in re.finditer(fn + r"\(\s*['\"](\w+)['\"]\s*,\s*['\"](" + UUID_RE.pattern + r")['\"]", js):
            role, uuid = m.group(1), m.group(2)
            assert table.get(role) == uuid, f"mxl-routes {fn} '{role}' fallback drifted from manifest"

    # legacy cam flow fallback must match program.legacy_cam_flow
    m = re.search(r"legacy_cam_flow\s*\)\s*\|\|\s*['\"](" + UUID_RE.pattern + r")['\"]", js)
    assert m and m.group(1) == man["program"]["legacy_cam_flow"], "mxl-routes legacy cam fallback drifted"


def test_layout_pgm_fallback_matches_manifest():
    man = _load_manifest()
    vf = {n: e["uuid"] for n, e in man["video_flows"].items() if isinstance(e, dict)}
    fb = _fallback_map("tools/layout_pgm.py", "_FALLBACK_FLOWS")
    assert fb, "could not parse layout_pgm _FALLBACK_FLOWS"
    for name, uuid in fb.items():
        assert vf.get(name) == uuid, f"layout_pgm fallback '{name}' drifted from manifest"
    text = (REPO / "tools/layout_pgm.py").read_text()
    m = re.search(r"_FALLBACK_DST\s*=\s*['\"](" + UUID_RE.pattern + r")['\"]", text)
    assert m and UUID_RE.search(m.group(0)).group(0) == vf["layout"], "layout_pgm DST drifted"


def test_single_dst_tool_fallbacks_match_manifest():
    """Tools that resolve one flow via `flow_uuid('kind','role')` keep the old
    literal in the except branch. Pin role -> literal -> manifest for each."""
    man = _load_manifest()
    vf = {n: e["uuid"] for n, e in man["video_flows"].items() if isinstance(e, dict)}
    cases = [
        ("tools/cam_ingest.py", "video", "cam"),
        ("tools/cam2_ingest.py", "video", "cam2"),
        ("tools/cam_relay.py", "video", "cam"),
    ]
    for path, kind, role in cases:
        text = (REPO / path).read_text()
        # the fallback literal is the UUID that appears in the except branch
        uuids = UUID_RE.findall(text)
        assert vf[role] in uuids, f"{path}: manifest {role} uuid {vf[role]} not present as fallback"

    # cam_relay also pins the legacy cam flow
    text = (REPO / "tools/cam_relay.py").read_text()
    assert man["program"]["legacy_cam_flow"] in UUID_RE.findall(text), "cam_relay legacy fallback drifted"


def test_layout_inputs_shape():
    """The live backend's 7-input slot list lives in the manifest. Pin its shape
    and labels (the 'public 4-input vs live 7-input' gap this work closed)."""
    man = _load_manifest()
    assert man["program"]["layout_inputs"] == ["cam", "playout", "pattern", "cam2", "guest1", "guest2", "layout"]
    assert len(man["program"]["layout_inputs"]) == len(man["program"]["layout_input_labels"])
    # every layout input must resolve to a real video flow
    vf = man["video_flows"]
    for role in man["program"]["layout_inputs"]:
        assert role in vf and "uuid" in vf[role], f"layout input '{role}' not a video flow"
    # mxl_vm must be the public control-plane IP the backend fetches, not the VNet IP
    assert man["network"]["mxl_vm"] == "20.64.205.144", "manifest mxl_vm must be the control-plane IP"


def test_server_enhanced_fallbacks_match_manifest():
    """The live backend (server-enhanced.js) derives its MXL UUIDs from the
    manifest with literal fallbacks. It's gitignored (live-only), so SKIP when
    it isn't checked out — this guard only runs where the file is present."""
    js_path = REPO / "backend" / "server-enhanced.js"
    if not js_path.is_file():
        import pytest
        pytest.skip("server-enhanced.js is gitignored / not present in this checkout")
    man = _load_manifest()
    vf = {n: e["uuid"] for n, e in man["video_flows"].items() if isinstance(e, dict)}
    af = {n: e["uuid"] for n, e in man["audio_flows"].items() if isinstance(e, dict)}
    js = js_path.read_text()
    for fn, table in ((r"_vf", vf), (r"_af", af)):
        for m in re.finditer(fn + r"\(\s*['\"](\w+)['\"]\s*,\s*['\"](" + UUID_RE.pattern + r")['\"]", js):
            role, uuid = m.group(1), m.group(2)
            assert table.get(role) == uuid, f"server-enhanced {fn} '{role}' fallback drifted"


def test_quickstart_guest_fallbacks_match_manifest():
    """quickstart.sh resolves GUEST1/2_FLOW from the manifest with literal
    fallbacks. Both must match the manifest guest flows (guest2 was the one value
    historically out of step — ...02 — now aligned to the facility standard ...01)."""
    man = _load_manifest()
    vf = {n: e["uuid"] for n, e in man["video_flows"].items() if isinstance(e, dict)}
    sh = (REPO / "scripts" / "quickstart.sh").read_text()
    for var, role in (("GUEST1_FLOW", "guest1"), ("GUEST2_FLOW", "guest2")):
        m = re.search(var + r"=\$\(_qfac\s+\w+\s+(" + UUID_RE.pattern + r")\)", sh)
        assert m, f"could not parse {var} fallback in quickstart.sh"
        assert m.group(1) == vf[role], f"quickstart {var} fallback drifted from manifest"
    # the stale ...02 must be gone
    assert "9e222e00-aaaa-4bbb-8ccc-000000000002" not in sh, "quickstart still has the stale guest2 ...02 UUID"


def test_flow_uuids_are_unique():
    """Two flows sharing a UUID would silently alias — a cut to one shows the
    other. Pin uniqueness across all video + audio flows and the legacy cam flow."""
    man = _load_manifest()
    seen = {}
    for sec in ("video_flows", "audio_flows"):
        for name, e in man[sec].items():
            if isinstance(e, dict) and "uuid" in e:
                key = f"{sec}.{name}"
                assert e["uuid"] not in seen, f"UUID collision: {key} == {seen[e['uuid']]}"
                seen[e["uuid"]] = key
    legacy = man["program"]["legacy_cam_flow"]
    assert legacy not in seen, f"legacy_cam_flow collides with {seen.get(legacy)}"


def test_control_ports_are_unique():
    man = _load_manifest()
    ports = [p["port"] for p in man["control_api"]["ports"].values()]
    assert len(ports) == len(set(ports)), "duplicate control-API port in manifest"
