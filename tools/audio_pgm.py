#!/usr/bin/env python3
"""Audio-follow-video for the MXL demo.

Writes a "PGM Audio" flow that always matches the video selector's program:
  slot 0 camera  -> silence (studio mic path TBD)
  slot 1 playout -> Clip Audio (the episode's real sound)
  slot 2 pattern -> TG Audio 1 (bars & tone, as broadcast intended)
Follows the video selector by polling its status API — no control surface needed,
cuts stay instant. Output PTS is a continuous sample counter locked to "now"+margin
so the encoder can read at the same index it reads video.
"""
import json
import sys
import threading
import time
import urllib.request
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

SEL_STATUS = 'http://172.17.0.1:9604/pipeline/status'
CLIP_AUDIO = '4a37a1ae-e0e1-59de-8354-c6884b25e551'   # Clip Audio (episode)
TG_AUDIO = '9b85cd39-2476-5467-b1cf-9eb8615dc07c'     # TG Audio 1 (tone)
DST = 'a0d10000-aaaa-4bbb-8ccc-000000000001'          # PGM Audio
CAPS = 'audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2,channel-mask=(bitmask)0x3'
MARGIN_NS = 66_000_000

Gst.init(None)
pipe = Gst.parse_launch(
    f'input-selector name=sel ! queue ! mxlsink name=sink domain=/mxl-domain '
    f'flow-id={DST} label="PGM Audio" description="audio follows video program" '
    f'group-hint="Audio-PGM:Audio" sync=false '
    f'audiotestsrc wave=silence is-live=true ! audioconvert ! audioresample ! {CAPS} ! queue ! sel.sink_0 '
    f'mxlsrc domain=/mxl-domain audio-flow-id={CLIP_AUDIO} ! audioconvert ! audioresample ! {CAPS} ! queue ! sel.sink_1 '
    f'mxlsrc domain=/mxl-domain audio-flow-id={TG_AUDIO} ! audioconvert ! audioresample ! {CAPS} ! queue ! sel.sink_2 ')
sel = pipe.get_by_name('sel')
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

def follow():
    cur = None
    while True:
        try:
            with urllib.request.urlopen(SEL_STATUS, timeout=2) as r:
                slot = json.load(r).get('active_input')
            if slot in (0, 1, 2) and slot != cur:
                pad = sel.get_static_pad(f'sink_{slot}')
                if pad:
                    sel.set_property('active-pad', pad)
                    cur = slot
                    print(f'audio follows -> slot {slot}', flush=True)
        except Exception:
            pass
        time.sleep(0.5)

threading.Thread(target=follow, daemon=True).start()
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (sys.stderr.write(f'ERR {m.parse_error()}\n'), loop.quit()))
pipe.set_state(Gst.State.PLAYING)
print('audio_pgm running', flush=True)
try:
    loop.run()
finally:
    pipe.set_state(Gst.State.NULL)
    sys.exit(1)
