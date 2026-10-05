"""Env-var configuration: defaults == the historical production values (except the four
required bring-up-mxl.sh site vars, which have no default), overrides work, and every variable is documented in docs/CONFIG.md."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "config" / "facility.json").read_text())


def _clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("MXL_", "TAMS_"))}
    e.update(extra)
    return e


# ── facility loaders ─────────────────────────────────────────────────────────
def _py_net(**env):
    code = ("import sys, json; sys.path.insert(0, 'tools'); import facility; "
            "print(json.dumps(facility.FACILITY['network']))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env=_clean_env(**env), check=True).stdout
    return json.loads(out)


def test_facility_py_defaults_are_manifest_values():
    assert _py_net() == MANIFEST["network"]


def test_facility_py_env_overrides():
    net = _py_net(MXL_VM_IP="203.0.113.9", MXL_VM_INTERNAL_IP="10.9.9.9", MXL_DOCKER_GATEWAY="172.18.0.1")
    assert net["mxl_vm"] == "203.0.113.9" and net["mxl_vm_internal"] == "10.9.9.9"
    assert net["docker_gateway"] == "172.18.0.1"
    assert _py_net(MXL_VM_IP="  ")["mxl_vm"] == MANIFEST["network"]["mxl_vm"]  # blank = unset


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_facility_js_matches_python_and_overrides():
    code = "console.log(JSON.stringify(require('./backend/facility').FACILITY.network))"
    run = lambda **env: json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True,
                                                  cwd=ROOT, env=_clean_env(**env), check=True).stdout)
    assert run() == MANIFEST["network"]
    assert run(MXL_VM_IP="203.0.113.9")["mxl_vm"] == "203.0.113.9"


# ── bring-up-mxl.sh site-config block ────────────────────────────────────────
BRING_UP_REQUIRED = {"MXL_VM_IP": "203.0.113.9", "MXL_SSH_KEY": "/h/.ssh/test-key",
                     "MXL_AZ_RESOURCE_GROUP": "rg-test", "MXL_AZ_VM_NAME": "vm-test"}


def _bring_up_probe():
    src = (ROOT / "scripts" / "bring-up-mxl.sh").read_text()
    m = re.search(r"(_required=\(MXL_VM_IP.*?\nMXL_HTML=[^\n]*\n)", src, re.S)
    assert m, "site-config block not found"
    return m.group(1) + 'printf "%s|%s|%s|%s|%s|%s|%s|%s|%s\\n" ' \
        '"$VM_IP" "$VM_USER" "$SITE_IP" "$MAKITO_IP" "$BACKEND_URL" "$FEED_URL" "$AZ_RG" "$AZ_VM" "$SSH"'


def _run_bring_up(**env):
    return subprocess.run(["bash", "-c", _bring_up_probe()], capture_output=True, text=True,
                          env=_clean_env(HOME="/h", **env))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_bring_up_required_vars_and_overrides():
    # with the four required vars set, every other default is unchanged and the ssh line is byte-identical in shape
    r = _run_bring_up(**BRING_UP_REQUIRED)
    assert r.returncode == 0, r.stderr
    d = r.stdout.strip().split("|")
    assert d[:8] == ["203.0.113.9", "guy", "50.106.4.50", "192.168.8.177", "https://prodbots.com",
                     "https://mxl-feed.cochran.cloud", "rg-test", "vm-test"]
    assert d[8] == "ssh -i /h/.ssh/test-key -o BatchMode=yes -o ConnectTimeout=8 guy@203.0.113.9"
    o = _run_bring_up(**{**BRING_UP_REQUIRED, "MXL_VM_IP": "198.51.100.7", "MXL_VM_SSH_USER": "ops",
                         "MXL_BACKEND_URL": "https://example.test/"})
    d = o.stdout.strip().split("|")
    assert d[0] == "198.51.100.7" and d[1] == "ops" and d[4] == "https://example.test"  # trailing / stripped
    assert d[8].endswith("ops@198.51.100.7")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_bring_up_fails_clearly_without_required_vars():
    r = _run_bring_up()
    assert r.returncode == 2 and r.stdout == ""
    for v in BRING_UP_REQUIRED:
        assert v in r.stderr, f"{v} not listed in the error"
    # only the missing ones are flagged in the "not set" list; blank counts as missing
    partial = {k: v for k, v in BRING_UP_REQUIRED.items() if k != "MXL_AZ_VM_NAME"}
    partial["MXL_SSH_KEY"] = ""  # set-but-empty counts as missing
    r = _run_bring_up(**partial)
    assert r.returncode == 2
    not_set = r.stderr.split("not set:")[1].split("Set all of these")[0]
    assert "MXL_AZ_VM_NAME" in not_set and "MXL_SSH_KEY" in not_set and "MXL_VM_IP" not in not_set


def test_bring_up_required_vars_have_no_defaults():
    src = (ROOT / "scripts" / "bring-up-mxl.sh").read_text()
    for v in BRING_UP_REQUIRED:
        assert not re.search(r"\$\{" + v + r":?[-=]", src), f"{v} must not have a default in bring-up-mxl.sh"


# ── python tools: defaults pinned (modules need GStreamer/boto3, so check source) ──
@pytest.mark.parametrize("rel,needles", [
    ("tools/tams_shipper.py", ["os.environ.get('TAMS_HOST', '20.112.83.140')", "f'http://{TAMS_HOST}:8000'",
                               "f'http://{TAMS_HOST}:9000'", "os.environ.get('TAMS_S3_USER', 'tams')"]),
    ("tools/backfill-mini.py", ["os.environ.get('TAMS_HOST','20.112.83.140')", "f'http://{_host}:9000'",
                                "os.environ.get('TAMS_S3_USER','tams')"]),
    ("tools/audio_pgm.py", ["os.environ.get('MXL_BACKEND_URL', 'https://prodbots.com')"]),
    ("tools/layout_pgm.py", ["os.environ.get('MXL_BACKEND_URL', 'https://prodbots.com')"]),
    ("tools/mxl_multiview.py", ["os.environ.get('MXL_BACKEND_URL', 'https://prodbots.com')"]),
    ("tools/mv_encode.py", ["os.environ.get('MXL_VM1_IP', '10.0.0.4')"]),
])
def test_tool_defaults_preserved(rel, needles):
    src = (ROOT / rel).read_text()
    for n in needles:
        assert n in src, f"{rel}: expected {n!r}"
    assert "'https://prodbots.com/api" not in src, f"{rel}: backend URL literal crept back in"


def test_layout_pgm_urls_resolve_to_same_endpoints():
    """BACKEND_URL + '/api/...' must reproduce the exact historical URLs."""
    src = (ROOT / "tools" / "layout_pgm.py").read_text()
    base = "https://prodbots.com"
    paths = re.findall(r"BACKEND_URL \+ '(/api/mxl/[a-z-]+)'", src)
    assert {"/api/mxl/layout-state", "/api/mxl/input", "/api/mxl/fade-done",
            "/api/mxl/status", "/api/mxl/repair"} <= set(paths)
    assert all(base + p for p in paths)


# ── documentation completeness ───────────────────────────────────────────────
NEW_VARS = [
    "MXL_VM_IP", "MXL_VM_INTERNAL_IP", "MXL_DOCKER_GATEWAY", "MXL_VM_SSH_USER", "MXL_SSH_KEY", "MXL_SITE_IP",
    "MXL_MAKITO_IP", "MXL_BACKEND_URL", "MXL_FEED_URL", "MXL_AZ_RESOURCE_GROUP", "MXL_AZ_VM_NAME", "MXL_HTML",
    "TAMS_HOST", "TAMS_S3_ENDPOINT", "TAMS_S3_USER", "MXL_VM1_IP", "MXL_VM2_IP", "MXL_VM1_SSH_USER",
    "MXL_MV_SRT_URL", "MXL_CONTROL_TOKEN", "MXL_CONTROL_REQUIRE_TOKEN", "MXL_REPAIR_RATE_MAX",
    "MXL_REPAIR_RATE_WINDOW_S", "MXL_GUEST1_SRT_PASSPHRASE", "MXL_GUEST2_SRT_PASSPHRASE",
    "MXL_GUEST_SRT_PASSPHRASE", "MXL_GRAPHICS_BIND",
]


def test_every_new_env_var_is_documented():
    doc = (ROOT / "docs" / "CONFIG.md").read_text()
    missing = [v for v in NEW_VARS if f"`{v}`" not in doc]
    assert not missing, f"undocumented in docs/CONFIG.md: {missing}"


# ── client side of MXL_CONTROL_TOKEN ────────────────────────────────────────
def test_contribution_core_sends_token_only_when_configured(monkeypatch):
    import contribution_core as cc
    base = {"Content-Type": "application/json"}
    monkeypatch.delenv("MXL_CONTROL_TOKEN", raising=False)
    assert cc._ctl_headers(base) == base
    monkeypatch.setenv("MXL_CONTROL_TOKEN", "abc")
    assert cc._ctl_headers(base) == {**base, "X-MXL-Token": "abc"}
    assert "X-MXL-Token" not in base  # input not mutated
