#!/usr/bin/env python3
"""Contributor ("guest") SRT ingest for the MXL demo switcher.

Anyone can push SRT to the demo's mediamtx (streamid publish:guest1 / guest2);
this script reads the resulting RTSP path, CONFORMS whatever arrives to the
chain's canonical 1080p30 v210 (videoscale letterboxes odd aspect ratios,
videorate reconciles any framerate), and writes it into the MXL domain as a
selector input — a stranger's feed becomes a cuttable camera.

Same cadence-preserving restamp as cam_ingest/cam2_ingest (+2-grain margin) so
the remote feed is instantly cuttable against local flows.

On first locked frame it POSTs the demo's public /api/mxl/repair (auto=1,
server-side cooldown) so the selector re-attaches and the guest button goes
live without an operator touching anything.

Usage: guest_ingest.py <path> <flow-uuid> <label> [jitterbuffer_ms]
   eg: guest_ingest.py guest1 9e111e00-aaaa-4bbb-8ccc-000000000001 "Guest 1"
"""
import json
import sys
import threading
import urllib.request
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

PATH = sys.argv[1] if len(sys.argv) > 1 else 'guest1'
DST = sys.argv[2] if len(sys.argv) > 2 else '9e111e00-aaaa-4bbb-8ccc-000000000001'
LABEL = sys.argv[3] if len(sys.argv) > 3 else 'Guest 1'
JITTER_MS = int(sys.argv[4]) if len(sys.argv) > 4 else 200
URL = f'rtsp://172.17.0.1:8554/{PATH}'
REPAIR_URL = 'https://prodbots.com/api/mxl/repair'
MARGIN_NS = 66_000_000  # 2 grains @30fps
RESYNC_NS = 150_000_000
RESYNC_COUNT = 45

Gst.init(None)
pipe = Gst.parse_launch(
    f'rtspsrc location={URL} latency={JITTER_MS} protocols=tcp name=src '
    f'! rtph264depay ! h264parse ! avdec_h264 max-threads=4 thread-type=frame '
    f'! queue max-size-buffers=8 ! videorate ! videoscale add-borders=true '
    f'! videoconvert n-threads=2 '
    f'! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
    f'pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709 '
    f'! mxlsink name=sink domain=/mxl-domain flow-id={DST} label="{LABEL}" '
    f'description="contributor SRT ingest ({PATH})" group-hint="{LABEL.replace(" ", "")}:Video" sync=false')
sink = pipe.get_by_name('sink')

state = {'offset': None, 'drift_n': 0, 'n': 0, 't0': None}

def announce():
    """Tell the backend a guest flow just appeared so the selector re-attaches.
    Keeps the current program slot; the backend rate-limits auto cascades
    (429 on cooldown) so we retry a few times rather than strand the feed."""
    import time as _t
    for attempt in range(4):
        try:
            r = urllib.request.Request(REPAIR_URL, data=json.dumps({'auto': 1}).encode(),
                                       headers={'Content-Type': 'application/json',
                                                'User-Agent': 'guest-ingest/1.0'})
            with urllib.request.urlopen(r, timeout=60) as resp:
                print(f'announce -> {resp.status} {resp.read()[:120]}', flush=True)
                return
        except urllib.error.HTTPError as e:
            print(f'announce {e.code} (attempt {attempt+1})', flush=True)
            if e.code not in (409, 429):
                return
        except Exception as e:
            print(f'announce err: {e} (attempt {attempt+1})', flush=True)
        _t.sleep(75)

def restamp(pad, info):
    buf = info.get_buffer()
    clock = pipe.get_clock()
    if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
        return Gst.PadProbeReturn.OK
    now = clock.get_time() - pipe.get_base_time()
    if state['offset'] is None:
        state['offset'] = now - buf.pts + MARGIN_NS
        print(f'cadence offset locked: {state["offset"]/1e6:.0f}ms', flush=True)
        threading.Thread(target=announce, daemon=True).start()
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
    if state['n'] % 300 == 0:
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
print(f'guest_ingest running: {URL} -> {DST} ("{LABEL}")', flush=True)
try:
    loop.run()
finally:
    pipe.set_state(Gst.State.NULL)
    sys.exit(1)  # abnormal by definition; supervisor restarts (idle 404 loop is cheap)
