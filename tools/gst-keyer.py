#!/usr/bin/env python3
"""gst-keyer — a lightweight GStreamer graphics keyer for MXL program.

Why this exists: the cbcrc html5-keyer composites an HTML/CSS graphic via a headless
Chromium (CEF). On a box without GPU-accelerated CEF it falls back to SwiftShader
software rendering and costs ~2+ cores while only hitting ~17 fps (and the CEF image
hardcodes the software GL backend, so a GPU doesn't help it). For a simple lower-third /
logo overlay that's wildly overbuilt.

This keyer does the same JOB — read the program flow, composite a graphic over it, write
the keyed program flow — with a plain GStreamer pipeline and NO browser:

    mxlsrc(program) → videoconvert → gdkpixbufoverlay(PNG w/ alpha) → videoconvert → mxlsink

It reads a PNG (with alpha) as the overlay — a lower-third, logo, bug, etc. — positioned
anywhere. Swap the PNG and toggle with /pipeline/key. Costs a few % of one core at 30 fps.
For animated/HTML graphics, the CEF keyer is still the tool; this is the cheap 90% case.

Env / args:
  --domain      MXL domain path        (default /mxl-domain)
  --in          input (program) flow UUID to read
  --out         output (keyed) flow UUID to write
  --png         overlay PNG path (RGBA; transparent where no graphic)   [optional]
  --x --y       overlay top-left position in px                         (default 0,0)
  --rate        grain rate numerator (fps)                              (default 30)
  --w --h       frame size                                              (default 1920x1080)

Toggle the overlay on/off at runtime by writing the PNG path (key on) or an empty/1x1
transparent PNG (key off) and SIGHUP — or just run two invocations. Kept deliberately
simple; the control plane (mxl-routes) can manage lifecycle like it does the CEF keyer.
"""
import argparse
import sys
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)


def build_pipeline(domain, in_uuid, out_uuid, png, x, y, num, w, h):
    # v210 is MXL's 10-bit 4:2:2; convert to a form the overlay understands, composite,
    # convert back to v210 for the sink. gdkpixbufoverlay blends the PNG's alpha.
    overlay = ""
    if png:
        overlay = (f"gdkpixbufoverlay location={png} offset-x={x} offset-y={y} "
                   f"overlay-width=0 overlay-height=0 ! ")
    desc = (
        f"mxlsrc domain={domain} video-flow-id={in_uuid} ! "
        f"video/x-raw,format=v210,width={w},height={h},framerate={num}/1 ! "
        f"videoconvert ! video/x-raw,format=BGRA ! "
        f"{overlay}"
        f"videoconvert ! video/x-raw,format=v210,width={w},height={h},framerate={num}/1 ! "
        f"queue max-size-buffers=4 leaky=downstream ! "
        f"mxlsink domain={domain} flow-id={out_uuid} sync=false "
        f'group-hint="Keyer:Video" description="program + graphics" label="Keyer PGM"'
    )
    return Gst.parse_launch(desc), desc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="/mxl-domain")
    ap.add_argument("--in", dest="in_uuid", required=True)
    ap.add_argument("--out", dest="out_uuid", required=True)
    ap.add_argument("--png", default="")
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--rate", type=int, default=30)
    ap.add_argument("--w", type=int, default=1920)
    ap.add_argument("--h", type=int, default=1080)
    a = ap.parse_args()

    pipe, desc = build_pipeline(a.domain, a.in_uuid, a.out_uuid, a.png, a.x, a.y, a.rate, a.w, a.h)
    print(f"gst-keyer: {desc}", flush=True)

    loop = GLib.MainLoop()
    bus = pipe.get_bus()
    bus.add_signal_watch()

    def on_msg(_bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"gst-keyer ERROR: {err} ({dbg})", flush=True)
            loop.quit()
        elif t == Gst.MessageType.EOS:
            print("gst-keyer EOS", flush=True)
            loop.quit()
    bus.connect("message", on_msg)

    pipe.set_state(Gst.State.PLAYING)
    print("gst-keyer running", flush=True)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipe.set_state(Gst.State.NULL)
    return 1  # non-zero so a supervising wrapper's `while :; do … done` restarts it


if __name__ == "__main__":
    sys.exit(main())
