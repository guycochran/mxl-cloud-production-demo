#!/usr/bin/env python3
"""MXL latency normalizer: re-publish the PTZ cam flow with PTS shifted forward
so its grain index aligns with locally-generated flows (makes it selector-cuttable).
Usage: cam_relay.py [offset_ns]"""
import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

OFFSET = int(sys.argv[1]) if len(sys.argv) > 1 else 2_100_000_000
SRC = '991e65d8-4fc4-58de-b22a-2d02f5952252'   # PTZ CAM Video
DST = 'ca111e00-aaaa-4bbb-8ccc-000000000001'   # CAM Live

Gst.init(None)
pipe = Gst.parse_launch(
    f'mxlsrc domain=/mxl-domain video-flow-id={SRC} ! queue ! '
    f'mxlsink name=sink domain=/mxl-domain flow-id={DST} label="CAM Live" '
    f'description="cam re-timed to now" group-hint="CameraLive:Video" sync=false')

def shift(pad, info):
    buf = info.get_buffer()
    if buf.pts != Gst.CLOCK_TIME_NONE:
        try:
            buf.pts += OFFSET
        except TypeError:  # non-writable buffer: replace with shifted copy
            nb = buf.copy()
            nb.pts = buf.pts + OFFSET
            info.get_buffer = lambda: nb
    return Gst.PadProbeReturn.OK

pipe.get_by_name('sink').get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, shift)

loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (sys.stderr.write(f'ERR {m.parse_error()}\n'), loop.quit()))
bus.connect('message::eos', lambda b, m: loop.quit())
pipe.set_state(Gst.State.PLAYING)
print(f'cam_relay running, offset={OFFSET}ns', flush=True)
try:
    loop.run()
finally:
    pipe.set_state(Gst.State.NULL)
    sys.exit(1)  # any exit is abnormal; supervisor should restart
