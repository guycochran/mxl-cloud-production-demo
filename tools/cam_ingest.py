#!/usr/bin/env python3
"""Low-latency camera ingest for the MXL demo.

Replaces the hls2mxl gateway pipeline + cam_relay double-hop for the camera:
  rtspsrc(latency=150) -> decode -> v210 -> [PTS := pipeline running time] -> mxlsink
Writing with PTS = "now" keeps the CAM Live head aligned with locally-generated
flows regardless of transport latency (self-tuning — no fixed offset), which is
what makes the camera an instantly-cuttable selector input.
Usage: cam_ingest.py [rtsp_url] [jitterbuffer_ms]
"""
import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

URL = sys.argv[1] if len(sys.argv) > 1 else 'rtsp://admin:Password@172.17.0.1:8554/cam1'
JITTER_MS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
# Stamp grains slightly ahead of "now": readers at now always find the grain
# written (prevents ring-slot stale reads = content flashes on missed indices).
MARGIN_NS = 66_000_000  # 2 grains @30fps
# Cadence rule: do NOT stamp per-frame arrival time — network/encode jitter would
# land straight in the grain indices (double/skipped grains = stutter during pans).
# Instead keep the stream's own regular PTS spacing and add ONE offset, computed
# from the first buffer and only re-synced if the mapping drifts persistently.
RESYNC_NS = 150_000_000   # re-sync when |now - mapped| exceeds this...
RESYNC_COUNT = 45         # ...for this many consecutive buffers (~1.5s)
DST = 'ca111e00-aaaa-4bbb-8ccc-000000000001'   # CAM Live (selector slot 0)

Gst.init(None)
pipe = Gst.Pipeline.new('cam-ingest')
src = Gst.ElementFactory.make('uridecodebin', 'src')
src.set_property('uri', URL)
q = Gst.ElementFactory.make('queue', 'q')
conv = Gst.ElementFactory.make('videoconvert', 'conv')
rate = Gst.ElementFactory.make('videorate', 'rate')  # camera-native fps (e.g. 60) -> exact 30
rate.set_property('drop-only', False)
caps = Gst.ElementFactory.make('capsfilter', 'caps')
caps.set_property('caps', Gst.Caps.from_string(
    'video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
    'interlace-mode=progressive,colorimetry=bt709'))
sink = Gst.ElementFactory.make('mxlsink', 'sink')
for k, v in dict(domain='/mxl-domain', **{'flow-id': DST}, label='CAM Live',
                 description='low-latency cam ingest', **{'group-hint': 'CameraLive:Video'}).items():
    sink.set_property(k, v)
sink.set_property('sync', False)
for e in (src, q, conv, rate, caps, sink):
    pipe.add(e)
q.link(conv); conv.link(rate); rate.link(caps); caps.link(sink)

def on_source_setup(_, source):
    if hasattr(source.props, 'latency'):
        source.set_property('latency', JITTER_MS)
        print(f'rtspsrc latency={JITTER_MS}ms', flush=True)
src.connect('source-setup', on_source_setup)

def on_pad(_, pad):
    s = pad.get_current_caps().get_structure(0)
    if s.get_name().startswith('video/') and not q.get_static_pad('sink').is_linked():
        pad.link(q.get_static_pad('sink'))
        print('video pad linked', flush=True)
src.connect('pad-added', on_pad)

state = {'offset': None, 'drift_n': 0}

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
    return Gst.PadProbeReturn.OK
sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, restamp)

loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (sys.stderr.write(f'ERR {m.parse_error()}\n'), loop.quit()))
bus.connect('message::eos', lambda b, m: (sys.stderr.write('EOS\n'), loop.quit()))
pipe.set_state(Gst.State.PLAYING)
print(f'cam_ingest running: {URL}', flush=True)
try:
    loop.run()
finally:
    pipe.set_state(Gst.State.NULL)
    sys.exit(1)  # abnormal by definition; supervisor restarts
