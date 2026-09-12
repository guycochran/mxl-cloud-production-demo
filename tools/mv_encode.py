#!/usr/bin/env python3
"""Multiview encoder leg: the ONE browser encoder for the whole wall.

Reads the "Multiview PGM" flow (written by mxl_multiview.py in the hls2mxl
container) out of the MXL domain and publishes it to VM1 mediamtx over SRT
as `publish:multiview` — the same contribution path guests use — so the wall
is viewable at mxl-feed.cochran.cloud/multiview/ (WebRTC) and the popout's
/mxlfeed/multiview/ proxy. Runs in the mxl2webrtc container (the only one
with x264enc). One encoder for 8 sources — that's the point of compositing
in the domain first.

PIPELINE IS PROVEN-VERBATIM (9/12, /tmp/pytest_mux4.py): do not "clean up".
The identity after mpegtsmux is LOAD-BEARING: without it srtsink's
negotiation stalls the tsmux aggregator and mediamtx gets headers but zero
media (the enc=375/mux=0 hunt). Leaky queues, n-threads, x264 threads, and
input restamping were all tried and are all either unnecessary or breaking.

Self-heal: mxl readers never survive their flow being recreated, and
mxl_multiview.py respawns on wedge (recreating the flow). If no TS packets
leave the mux for 12s, exit — the supervisor respawns us with a fresh attach.
"""
import time
import threading
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

SRC = 'ab900700-aaaa-4bbb-8ccc-000000000001'   # Multiview PGM
SRT = 'srt://10.0.0.4:8890?streamid=publish:multiview&latency=200'

Gst.init(None)

pipe = Gst.parse_launch(
    f'mxlsrc domain=/mxl-domain video-flow-id={SRC} '
    f'! video/x-raw,format=v210 ! queue ! videoconvert ! video/x-raw,format=I420 '
    f'! x264enc tune=zerolatency speed-preset=ultrafast bitrate=3000 key-int-max=30 '
    # alignment=7 → 1316-byte (7x188) SRT-standard payloads. mediamtx SILENTLY
    # swallows the default single-188-byte packets: publisher looks live,
    # readers get zero bytes (the final boss of the 9/12 hunt).
    f'! identity name=postenc ! h264parse config-interval=1 ! mpegtsmux alignment=7 '
    f'! identity name=postmux '
    f'! srtsink uri="{SRT}" wait-for-connection=false')

counts = {'postenc': 0, 'postmux': 0}
last = {'t': time.monotonic()}


def make_counter(key):
    def probe(pad, info):
        counts[key] += 1
        if key == 'postmux':
            last['t'] = time.monotonic()
        return Gst.PadProbeReturn.OK
    return probe


for k in counts:
    pipe.get_by_name(k).get_static_pad('src') \
        .add_probe(Gst.PadProbeType.BUFFER, make_counter(k))


def watchdog():
    time.sleep(25)  # attach + first grains + first IDR
    while True:
        time.sleep(5)
        if time.monotonic() - last['t'] > 12:
            print(f'no TS out 12s (flow recreated / mux stalled) — exiting for '
                  f'fresh attach (postenc={counts["postenc"]} postmux={counts["postmux"]})', flush=True)
            import os
            os._exit(1)


threading.Thread(target=watchdog, daemon=True).start()

pipe.set_state(Gst.State.PLAYING)
print('mv_encode running -> ' + SRT, flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::latency', lambda b, m: pipe.recalculate_latency())
bus.connect('message::error', lambda b, m: (print(f'gst error: {m.parse_error()[0]}', flush=True), loop.quit()))
bus.connect('message::eos', lambda b, m: (print('EOS', flush=True), loop.quit()))
loop.run()
raise SystemExit(1)
