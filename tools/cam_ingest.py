#!/usr/bin/env python3
"""Camera 2 (static SDI shot via Haivision Makito X4) ingest for the MXL demo.

Same restamp pattern as cam_ingest.py (cadence-preserving PTS offset, +2-grain
margin) with two cam2-specific differences:
  * explicit rtspsrc->rtph264depay->h264parse->avdec_h264 chain instead of
    uridecodebin — autoplugging picked a decoder that couldn't hold 30fps
    (steady ~1s cadence re-syncs).  avdec_h264 gets frame threading explicitly.
  * videorate reconciles the stream's 29.97 (30000/1001) framerate to the
    chain's exact 30/1 grain rate.  The camera is PROGRESSIVE 1080p30, but its
    framerate caps are still 30000/1001 — without videorate the v210 capsfilter
    (framerate=30/1) intermittently fails to negotiate when the decoder
    re-presents 30000/1001 caps (~80s cadence), crashing the pipeline.
Usage: cam2_ingest.py [rtsp_url] [jitterbuffer_ms]
"""
import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

URL = sys.argv[1] if len(sys.argv) > 1 else 'rtsp://admin:Password@172.17.0.1:8554/cam1'
JITTER_MS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
MARGIN_NS = 66_000_000  # 2 grains — ring window is small; bigger margins starve READERS (166ms => "too late" wedges)
RESYNC_NS = 150_000_000
RESYNC_COUNT = 45
DST = 'ca111e00-aaaa-4bbb-8ccc-000000000001'   # CAM Live (selector slot 0)

Gst.init(None)
pipe = Gst.parse_launch(
    f'rtspsrc location={URL} latency={JITTER_MS} protocols=tcp name=src '
    f'! rtph264depay ! h264parse ! avdec_h264 max-threads=4 thread-type=frame '
    f'! queue max-size-buffers=8 ! videorate ! videoconvert n-threads=2 '
    f'! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
    f'interlace-mode=progressive,colorimetry=bt709 '
    f'! mxlsink name=sink domain=/mxl-domain flow-id={DST} label="CAM Live" '
    f'description="low-latency cam ingest" group-hint="CameraLive:Video" sync=false')
sink = pipe.get_by_name('sink')

state = {'offset': None, 'drift_n': 0, 'n': 0, 't0': None}

def restamp(pad, info):
    buf = info.get_buffer()
    clock = pipe.get_clock()
    if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
        return Gst.PadProbeReturn.OK
    now = clock.get_time() - pipe.get_base_time()
    if state['offset'] is None:
        state['offset'] = now - buf.pts + MARGIN_NS
        print(f'cadence offset locked: {state["offset"]/1e6:.0f}ms', flush=True)
    mapped = buf.pts + state['offset']
    err = now + MARGIN_NS - mapped
    if abs(err) > RESYNC_NS:
        state['drift_n'] += 1
        if state['drift_n'] >= RESYNC_COUNT:
            state['offset'] += err
            state['drift_n'] = 0
            print(f'cadence re-synced by {err/1e6:.0f}ms', flush=True)
            mapped = buf.pts + state['offset']
    else:
        state['drift_n'] = 0
    buf.pts = mapped
    state['n'] += 1
    if state['t0'] is None:
        state['t0'] = now
    if state['n'] % 150 == 0:
        el = (now - state['t0']) / 1e9
        fps = state['n'] / el if el > 0 else 0
        print(f"diag n={state['n']} fps={fps:.2f} err={err/1e6:.0f}ms", flush=True)
    return Gst.PadProbeReturn.OK
sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, restamp)

loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (sys.stderr.write(f'ERR {m.parse_error()}\n'), loop.quit()))
bus.connect('message::eos', lambda b, m: (sys.stderr.write('EOS\n'), loop.quit()))
pipe.set_state(Gst.State.PLAYING)
print(f'cam_ingest running (explicit avdec_h264): {URL}', flush=True)
try:
    loop.run()
finally:
    pipe.set_state(Gst.State.NULL)
    sys.exit(1)  # abnormal by definition; supervisor restarts
