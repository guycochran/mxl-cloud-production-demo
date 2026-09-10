#!/usr/bin/env python3
"""PGM Lite: 960x540 copy of the Keyer PGM for bandwidth-limited fabric
receivers. Full 1080p30 v210 is ~1.32 Gbps — MORE than 1GbE; this quarter-
resolution flow is ~0.33 Gbps and fits comfortably on a GigE laptop at a
booth. Park it; launch on demand before starting a lite fabric leg.

Flow: 119070e0-aaaa-4bbb-8ccc-000000000001  "PGM Lite"
"""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

SRC = '5c73394e-85df-50a3-8988-5edde5b5522a'   # Keyer PGM
DST = '119070e0-aaaa-4bbb-8ccc-000000000001'
MARGIN_NS = 66_000_000

Gst.init(None)
pipe = Gst.parse_launch(
    f'mxlsrc domain=/mxl-domain video-flow-id={SRC} ! queue max-size-buffers=4 ! '
    f'videoscale ! videoconvert n-threads=2 ! '
    f'video/x-raw,format=v210,width=960,height=540,framerate=30/1,'
    f'pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709 ! '
    f'mxlsink name=sink domain=/mxl-domain flow-id={DST} label="PGM Lite" '
    f'description="960x540 program for bandwidth-limited fabric receivers" '
    f'group-hint="PGMLite:Video" sync=false')
sink = pipe.get_by_name('sink')
st = {'off': None}


def normalize(pad, info):
    buf = info.get_buffer()
    clock = pipe.get_clock()
    if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
        return Gst.PadProbeReturn.OK
    now = clock.get_time() - pipe.get_base_time()
    if st['off'] is None or abs((buf.pts + st['off']) - (now + MARGIN_NS)) > 500_000_000:
        st['off'] = now + MARGIN_NS - buf.pts
    buf.pts += st['off']
    return Gst.PadProbeReturn.OK


sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, normalize)
pipe.set_state(Gst.State.PLAYING)
print('pgm_lite running', flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (print(f'gst error: {m.parse_error()}', flush=True), loop.quit()))
loop.run()
raise SystemExit(1)
