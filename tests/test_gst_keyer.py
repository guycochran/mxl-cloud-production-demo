"""gst-keyer.py invariants — the lightweight GStreamer graphics keyer that replaces the
CEF/Chromium html5-keyer for simple PNG overlays (full 30fps at ~1 core vs CEF's ~2 cores
/ 17fps software rendering). Text-scanned: running it needs GStreamer + mxlsrc/mxlsink.
"""
import ast
from pathlib import Path

KEYER = Path(__file__).resolve().parent.parent / "tools" / "gst-keyer.py"


def test_exists_and_parses():
    assert KEYER.is_file(), "tools/gst-keyer.py missing"
    ast.parse(KEYER.read_text())  # valid python


def test_reads_and_writes_mxl_flows():
    """The keyer's I/O contract: read a program flow (mxlsrc), write the keyed flow
    (mxlsink) — a drop-in for what the CEF keyer produces."""
    s = KEYER.read_text()
    assert "mxlsrc" in s and "mxlsink" in s, "must read via mxlsrc and write via mxlsink"
    assert "video-flow-id=" in s and "flow-id=" in s, "must wire input + output flow UUIDs"


def test_composites_png_overlay_no_browser():
    """Composites a PNG (with alpha) via gdkpixbufoverlay — NO CEF/Chromium/browser.
    (Scan code lines, not the docstring, which legitimately explains what it replaces.)"""
    s = KEYER.read_text()
    assert "gdkpixbufoverlay" in s, "must use gdkpixbufoverlay for the graphic"
    # strip the module docstring, then ensure no CEF/Chromium element in the actual code
    tree = ast.parse(s)
    body = s
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(getattr(tree.body[0], "value", None), ast.Constant)):
        body = s.replace(ast.get_docstring(tree) or "", "", 1)
    low = body.lower()
    assert "cefsrc" not in low and "gstcef" not in low and "chromium" not in low, \
        "the pipeline must not use a CEF/Chromium element"


def test_overlay_is_optional():
    """No PNG → clean passthrough (keyer with key off), so it can run without a graphic."""
    s = KEYER.read_text()
    assert "if png" in s or "if a.png" in s or "overlay = \"\"" in s, \
        "overlay must be optional (empty when no PNG)"


def test_pins_v210_and_framerate():
    """MXL program is 10-bit v210; the pipeline must convert to/from it and pin the rate."""
    s = KEYER.read_text()
    assert "v210" in s, "must handle v210 (MXL program format)"
    assert "framerate=" in s, "must pin the grain/frame rate"
