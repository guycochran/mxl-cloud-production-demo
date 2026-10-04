"""The local control plane (backend/local-server.js + web/local.html) is the
self-contained switcher UI an adopter gets from the open repo — no prodbots, no
external services. These checks keep that promise without needing Node: they scan
the files as text. (A live smoke test needs express + a running facility.)
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTML = REPO / "web" / "local.html"
SERVER = REPO / "backend" / "local-server.js"
PKG = REPO / "backend" / "package.json"


def test_files_exist():
    assert HTML.is_file(), "web/local.html missing"
    assert SERVER.is_file(), "backend/local-server.js missing"
    assert PKG.is_file(), "backend/package.json missing (express dep)"


def test_ui_is_self_contained():
    """No external hosts — the UI must work on an air-gapped quickstart box.
    Only localhost, the w3.org XML namespace, and data: URIs are allowed."""
    html = HTML.read_text()
    urls = re.findall(r"https?://[a-z0-9.\-]+", html, re.I)
    external = [u for u in urls if not re.search(r"(127\.0\.0\.1|localhost|w3\.org)", u)]
    assert not external, f"local.html references external hosts: {external}"
    assert "cdn" not in html.lower(), "local.html pulls from a CDN"


def test_ui_uses_only_open_routes():
    """The UI must drive the facility through the portable /api/mxl/* routes +
    the /api/mxl/slots helper the local server adds — not prodbots-only endpoints."""
    html = HTML.read_text()
    called = set(re.findall(r"/api/mxl/([a-z]+)", html))
    allowed = {"status", "input", "key", "pattern", "repair", "slots"}
    extra = called - allowed
    assert not extra, f"local.html calls non-portable routes: {extra}"


def test_server_mounts_routes_and_serves_ui():
    src = SERVER.read_text()
    assert "require('./mxl-routes')" in src, "local-server doesn't mount mxl-routes"
    assert "local.html" in src, "local-server doesn't serve the UI"
    assert "127.0.0.1" in src or "MXL_CONTROL_BIND" in src, "no bind address handling"


def test_package_pins_express():
    import json
    pkg = json.loads(PKG.read_text())
    assert "express" in pkg.get("dependencies", {}), "express not pinned in package.json"
