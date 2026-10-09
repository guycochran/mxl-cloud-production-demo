"""The local control plane (backend/local-server.js + web/local.html) is the
self-contained switcher UI an adopter gets from the open repo — no prodbots, no
external services. These checks keep that promise without needing Node: they scan
the files as text. (A live smoke test needs express + a running facility.)
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTML = REPO / "web" / "local.html"
HEALTH_HTML = REPO / "web" / "health.html"
SERVER = REPO / "backend" / "local-server.js"
ROUTES = REPO / "backend" / "mxl-routes.js"
HEALTH_INFO = REPO / "backend" / "health-info.js"
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


# ── Health Skin (read-only monitor, separate page from the control Skin) ──────

def test_health_skin_exists_and_is_separate():
    """The grain/system monitor is its own page + its own read-only endpoint — it
    must NOT leak control into the Core Skin (local.html drives only control
    routes). Keeping health separate is the whole point of 'Core vs Skin'."""
    assert HEALTH_HTML.is_file(), "web/health.html missing"
    assert HEALTH_INFO.is_file(), "backend/health-info.js missing"
    # the control Skin must not start calling /api/mxl/health — health is its own page
    assert "/api/mxl/health" not in HTML.read_text(), \
        "the control Skin (local.html) should not poll health — keep it separate"


def test_health_skin_is_self_contained():
    """Same air-gap promise as the control Skin: no external hosts, self-hosted
    fonts only, no CDN."""
    html = HEALTH_HTML.read_text()
    urls = re.findall(r"https?://[a-z0-9.\-]+", html, re.I)
    external = [u for u in urls if not re.search(r"(127\.0\.0\.1|localhost|w3\.org)", u)]
    assert not external, f"health.html references external hosts: {external}"
    assert "cdn" not in html.lower(), "health.html pulls from a CDN"
    woff2 = list(FONTS.glob("*.woff2"))
    for f in woff2:
        assert f.name in html, f"health.html doesn't @font-face {f.name}"


def test_health_endpoint_is_read_only():
    """The health endpoint + page must be GET-only — a monitor never mutates the
    facility. No POST/PUT/DELETE handlers touch the health surface."""
    src = SERVER.read_text()
    assert "app.get('/api/mxl/health'" in src, "local-server missing GET /api/mxl/health"
    assert "app.get('/health'" in src, "local-server doesn't serve /health page"
    # no mutating verb on the health paths
    for verb in ("post", "put", "delete", "patch"):
        assert f"app.{verb}('/api/mxl/health'" not in src, \
            f"health endpoint must not expose {verb.upper()}"


def test_health_reuses_grain_probe_snapshot():
    """Cost control: the expensive grain measurement is grain_probe.py's job. The
    endpoint must READ its snapshot (grains.json), not spawn its own probes."""
    src = SERVER.read_text()
    assert "grains.json" in src, "health endpoint doesn't read the grain-probe snapshot"
    # must cache so N viewers collapse to one scrape
    assert "HEALTH_TTL" in src or "_healthCache" in src, \
        "health endpoint has no server-side cache (N viewers would each scrape)"
