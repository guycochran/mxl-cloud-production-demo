"""make-lower-third.py — the cairo-based lower-third PNG generator for gst-keyer. Text-scan
(running it needs pycairo + a font); verifies it's the Skin layer: cairo, RGBA, no browser."""
import ast
from pathlib import Path
T = Path(__file__).resolve().parent.parent / "tools" / "make-lower-third.py"

def test_exists_and_parses():
    assert T.is_file(); ast.parse(T.read_text())

def test_uses_cairo_rgba_no_browser():
    s = T.read_text()
    assert "import cairo" in s, "must use pycairo"
    assert "FORMAT_ARGB32" in s, "must render RGBA (alpha for overlay)"
    assert "write_to_png" in s, "must output a PNG"
    assert "cef" not in s.lower() and "chrom" not in s.lower(), "no browser dependency"

def test_takes_title_and_subtitle():
    s = T.read_text()
    assert "--title" in s and "--subtitle" in s, "customizable text (the Skin layer)"
