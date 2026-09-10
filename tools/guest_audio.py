#!/usr/bin/env python3
"""Guest AUDIO ingest: pulls a contributor's audio from VM2's mediamtx over
the VNet and writes it as an MXL audio flow for the program mixer.

Usage: guest_audio.py <path> <flow-uuid> <label>
  e.g. guest_audio.py guest1 a1111e00-aaaa-4bbb-8ccc-000000000001 "Guest 1 Audio"

Same restamp/cadence discipline as audio_pgm (offset locked to now+margin).
Supervised: exits on error/EOS, the runner respawns; while the contributor is
offline the RTSP 404s and we just retry — the flow is absent, and the mixer
tolerates that.
"""
import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

PATH = sys.argv[1]
DST = sys.argv[2]
LABEL = sys.argv[3]
URL = f'rtsp://10.0.0.5:8554/{PATH}'   # VM2 mediamtx over the VNet
CAPS = 'audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2,channel-mask=(bitmask)0x3'
MARGIN_NS = 66_000_000

Gst.init(None)
pipe = Gst.parse_launch(
    f'rtspsrc location={URL} latency=300 protocols=tcp name=src '
    f'! decodebin ! audioconvert ! audioresample ! {CAPS} ! queue max-size-buffers=32 '
    f'! mxlsink name=sink domain=/mxl-domain flow-id={DST} label="{LABEL}" '
    f'description="contributor audio ({PATH})" group-hint="{LABEL.replace(" ", "")}:Audio" sync=false')
sink = pipe.get_by_name('sink')

state = {'next_pts': None}


def restamp(pad, info):
    buf = info.get_buffer()
    clock = pipe.get_clock()
    if not clock:
        return Gst.PadProbeReturn.OK
    now = clock.get_time() - pipe.get_base_time()
    if state['next_pts'] is None or abs(now + MARGIN_NS - state['next_pts']) > 500_000_000:
        state['next_pts'] = now + MARGIN_NS
    buf.pts = state['next_pts']
    if buf.duration != Gst.CLOCK_TIME_NONE:
        state['next_pts'] += buf.duration
    return Gst.PadProbeReturn.OK


sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, restamp)

pipe.set_state(Gst.State.PLAYING)
print(f'guest_audio running: {URL} -> {DST} ("{LABEL}")', flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (print(f'ERR {m.parse_error()}', flush=True), loop.quit()))
bus.connect('message::eos', lambda b, m: (print('EOS', flush=True), loop.quit()))
loop.run()
raise SystemExit(1)
