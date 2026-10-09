#!/usr/bin/env python3
"""make-lower-third.py — render a lower-third overlay PNG (RGBA, 1920x1080) with cairo.

A full-frame transparent canvas with a lower-third graphic baked in at the bottom-left,
so tools/gst-keyer.py can composite it over program with gdkpixbufoverlay at offset 0,0.
No browser, no PIL — just pycairo (present in the keyer image) + a system font.

    python3 make-lower-third.py --title "GUEST NAME" --subtitle "Role / Location" \
        --out /tmp/lower-third.png

Customize freely — this is the "Skin" layer (colors/brand), kept out of the generic Core.
"""
import argparse
import cairo

W, H = 1920, 1080

# palette (matches the Skin's dark theme)
BG = (0x0b / 255, 0x10 / 255, 0x18 / 255, 0.82)   # panel, mostly opaque
ACCENT = (0x35 / 255, 0xe0 / 255, 0xff / 255, 1.0)  # cyan stripe
INK = (0xe8 / 255, 0xee / 255, 0xff / 255, 1.0)     # title
DIM = (0x9a / 255, 0xa6 / 255, 0xc2 / 255, 1.0)     # subtitle


def rounded(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -1.5708, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, 1.5708)
    ctx.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    ctx.arc(x + r, y + r, r, 3.1416, 4.7124)
    ctx.close_path()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="MXL SWITCHER")
    ap.add_argument("--subtitle", default="Live from one $400 box")
    ap.add_argument("--out", default="/tmp/lower-third.png")
    # geometry (bottom-left lower third)
    ap.add_argument("--x", type=int, default=90)
    ap.add_argument("--y", type=int, default=865)
    ap.add_argument("--w", type=int, default=860)
    ap.add_argument("--h", type=int, default=120)
    a = ap.parse_args()

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surf)
    # (canvas starts fully transparent)

    bx, by, bw, bh = a.x, a.y, a.w, a.h
    # drop shadow
    ctx.set_source_rgba(0, 0, 0, 0.35)
    rounded(ctx, bx + 5, by + 6, bw, bh, 12)
    ctx.fill()
    # panel
    ctx.set_source_rgba(*BG)
    rounded(ctx, bx, by, bw, bh, 12)
    ctx.fill()
    # accent stripe (left edge)
    ctx.set_source_rgba(*ACCENT)
    rounded(ctx, bx, by, 14, bh, 7)
    ctx.fill()

    tx = bx + 40
    # title
    ctx.select_font_face("Liberation Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(46)
    ctx.set_source_rgba(*INK)
    ctx.move_to(tx, by + 58)
    ctx.show_text(a.title)
    # subtitle
    ctx.select_font_face("Liberation Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(26)
    ctx.set_source_rgba(*DIM)
    ctx.move_to(tx, by + 96)
    ctx.show_text(a.subtitle)

    surf.write_to_png(a.out)
    print(f"wrote {a.out} ({a.title!r} / {a.subtitle!r})")


if __name__ == "__main__":
    main()
