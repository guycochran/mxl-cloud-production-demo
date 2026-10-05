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
ROUTES = REPO / "backend" / "mxl-routes.js"
PKG = REPO / "backend" / "package.json"
FONTS = REPO / "web" / "fonts"


def test_files_exist():
    assert HTML.is_file(), "web/local.html missing"
    assert SERVER.is_file(), "backend/local-server.js missing"
    assert PKG.is_file(), "backend/package.json missing (express dep)"


def test_fonts_are_self_hosted():
    """The UI uses Barlow Condensed + IBM Plex Mono; they must ship as local
    woff2 (no CDN) so the air-gap promise holds. The LICENSE (OFL) must be there."""
    assert FONTS.is_dir(), "web/fonts/ missing"
    woff2 = list(FONTS.glob("*.woff2"))
    assert len(woff2) >= 4, f"expected >=4 bundled woff2, found {len(woff2)}"
    assert (FONTS / "LICENSE").is_file(), "web/fonts/LICENSE (OFL) missing"
    html = HTML.read_text()
    for f in woff2:
        assert f.name in html, f"local.html doesn't @font-face {f.name}"


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
    the /api/mxl/slots and /api/mxl/thumbs helpers the local server adds — not
    prodbots-only endpoints."""
    html = HTML.read_text()
    called = set(re.findall(r"/api/mxl/([a-z]+)", html))
    allowed = {"status", "input", "preview", "take", "warmup", "key", "pattern",
               "repair", "slots", "thumbs", "ingest"}
    extra = called - allowed
    assert not extra, f"local.html calls non-portable routes: {extra}"


def test_ui_has_pvw_pgm_take():
    """News-grade switcher = preview/program dual-bus. The UI must drive the
    preview + take routes (not just hot-cut) and render a multiview."""
    html = HTML.read_text()
    assert "/api/mxl/preview" in html, "UI doesn't arm the preview bus"
    assert "/api/mxl/take" in html, "UI has no TAKE"
    assert "/api/mxl/thumbs/" in html, "UI has no multiview thumbnails"


def test_server_mounts_routes_and_serves_ui():
    src = SERVER.read_text()
    assert "require('./mxl-routes')" in src, "local-server doesn't mount mxl-routes"
    assert "local.html" in src, "local-server doesn't serve the UI"
    assert "/api/mxl/thumbs/" in src, "local-server doesn't serve multiview thumbnails"


def test_server_defaults_to_localhost():
    """The control API has no auth — the server must bind 127.0.0.1 by default,
    only opening to the LAN when MXL_CONTROL_BIND is set explicitly."""
    src = SERVER.read_text()
    assert re.search(r"MXL_CONTROL_BIND\s*\|\|\s*'127\.0\.0\.1'", src), \
        "local-server default bind is not 127.0.0.1"


def test_routes_define_preview_and_take():
    """The open route module must expose the dual-bus control surface + per-slot
    live, on its own (no proprietary server-enhanced.js)."""
    src = ROUTES.read_text()
    assert "/api/mxl/preview" in src, "mxl-routes missing POST /api/mxl/preview"
    assert "/api/mxl/take" in src, "mxl-routes missing POST /api/mxl/take"
    # status must surface pvw + per-slot live for the UI's tally
    assert "pvw" in src, "mxl-routes has no preview-bus state"
    assert "slots" in src and "live" in src, "status doesn't report slots[].live"


def test_routes_have_warmup():
    """The cold-reader wedge fix: a warmup sweep must exist as a fallback so a cut
    can't stick on the previous source after a flow is recreated (seen on HW Oct 4)."""
    src = ROUTES.read_text()
    assert "/api/mxl/warmup" in src, "mxl-routes missing POST /api/mxl/warmup"
    assert "active-input" in src and "400" in src, "warmup doesn't sweep readers"


def test_cut_prewarms_target():
    """The primary wedge fix: a cut must pre-warm the destination reader before the
    real cut (gentle — only the target slot, so it can't blip the WebRTC relay like
    the full sweep does). Tunable/opt-out via MXL_PREWARM."""
    src = ROUTES.read_text()
    assert "MXL_PREWARM" in src, "cut has no pre-warm (cold-reader wedge fix missing)"
    assert "prewarm" in src.lower(), "pre-warm logic not in mxlSetInput"


def test_package_pins_express():
    import json
    pkg = json.loads(PKG.read_text())
    assert "express" in pkg.get("dependencies", {}), "express not pinned in package.json"
