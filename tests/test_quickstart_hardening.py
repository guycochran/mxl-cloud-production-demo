# SPDX-License-Identifier: Apache-2.0
"""Hardening guards for scripts/quickstart.sh and the browser pages.

Hardware-free: extracts the small, self-contained pieces (graphics server, guest SRT
passphrase config renderer) and exercises them directly.
"""
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QS = (ROOT / "scripts" / "quickstart.sh").read_text()
BASH = shutil.which("bash")


def _block(start, end):
    m = re.search(re.escape(start) + r".*?\n(.*?)\n" + re.escape(end), QS, re.S)
    assert m, f"marker {start!r} not found in quickstart.sh"
    return m.group(1)


# ── (b) graphics server ──────────────────────────────────────────────────────
def _graphics_server_src():
    m = re.search(r"graphics_server\.py\" <<'PY'\n(.*?)\nPY\n", QS, re.S)
    assert m, "embedded graphics_server.py heredoc not found"
    return m.group(1)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_graphics_server_serves_files_but_not_listings(tmp_path):
    (tmp_path / "lower-third.html").write_text("<p>lt</p>")
    sub = tmp_path / "sub"; sub.mkdir(); (sub / "a.txt").write_text("a")
    script = tmp_path.parent / f"gs_{tmp_path.name}.py"
    script.write_text(_graphics_server_src())
    port = _free_port()
    proc = subprocess.Popen(["python3", str(script), str(port), "127.0.0.1", str(tmp_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/lower-third.html", timeout=1); break
            except Exception:
                time.sleep(0.1)
        assert urllib.request.urlopen(base + "/lower-third.html").read() == b"<p>lt</p>"
        assert urllib.request.urlopen(base + "/sub/a.txt").read() == b"a"
        for d in ("/", "/sub/", "/sub"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + d)
            assert e.value.code == 404, d
    finally:
        proc.terminate(); proc.wait(5)


def test_graphics_not_bound_to_all_interfaces_by_default():
    # the only 0.0.0.0 path must be the explicit opt-in value
    assert "--bind 0.0.0.0" not in QS
    assert 'GRAPHICS_BIND="${MXL_GRAPHICS_BIND:-}"' in QS
    assert "python3 -m http.server 8085" not in QS


# ── (c) guest SRT passphrase ─────────────────────────────────────────────────
GUEST_BLOCK = None


def _render(env):
    block = _block("# --- BEGIN guest-srt-conf", "# --- END guest-srt-conf ---")
    e = {k: v for k, v in os.environ.items() if not k.startswith("MXL_")}
    e.update(env)
    r = subprocess.run([BASH, "-c", block + "\nrender_mediamtx_guest_conf"],
                       capture_output=True, text=True, env=e)
    return r


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_srt_default_is_unchanged_no_config():
    r = _render({})
    assert r.returncode == 0 and r.stdout == ""


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_srt_per_guest_and_shared_passphrase():
    r = _render({"MXL_GUEST1_SRT_PASSPHRASE": "guest-one-secret"})
    assert r.returncode == 0
    assert "guest1:" in r.stdout and "guest-one-secret" in r.stdout and "guest2:" not in r.stdout
    assert "all_others:" in r.stdout  # other paths keep default behaviour
    r = _render({"MXL_GUEST_SRT_PASSPHRASE": "shared-secret-123", "MXL_GUEST2_SRT_PASSPHRASE": "override-secret-2"})
    assert "guest1:" in r.stdout and "shared-secret-123" in r.stdout
    assert "override-secret-2" in r.stdout  # per-guest wins for guest2
    assert r.stdout.count("shared-secret-123") == 1


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("bad", ["short", "x" * 80, "has'quote-123456", 'has"quote-123456', "back\\slash-123456"])
def test_srt_rejects_invalid_passphrases(bad):
    r = _render({"MXL_GUEST_SRT_PASSPHRASE": bad})
    assert r.returncode != 0
    assert "passphrase must be 10-79" in r.stderr


def test_srt_conf_is_mounted_only_when_configured():
    assert 'MTX_CONF_ARGS=()' in QS and '"${MTX_CONF_ARGS[@]}"' in QS


# ── (d) pinned + SRI scripts ─────────────────────────────────────────────────
@pytest.mark.parametrize("page", ["web/mxl-clip.html", "web/mxl-tams.html"])
def test_cdn_scripts_are_pinned_with_sri(page):
    html = (ROOT / page).read_text()
    tags = re.findall(r'<script[^>]+src="(https://cdn\.[^"]+)"[^>]*>', html)
    assert tags, "expected CDN script tags"
    for m in re.finditer(r'<script[^>]+src="(https://cdn\.[^"]+)"[^>]*>', html):
        tag, url = m.group(0), m.group(1)
        assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag, url
        assert re.search(r"@\d+\.\d+\.\d+/|@[0-9a-f]{40}/", url), f"unpinned: {url}"
