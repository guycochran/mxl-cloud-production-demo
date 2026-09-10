#!/usr/bin/env python3
"""Program AUDIO MIXER for the MXL demo (v2 — replaces audio-follow-video).

Writes the "PGM Audio" flow the encoder already consumes, but as a live MIX:
  - silence anchor (is-live; keeps the mixer's clock ticking always)
  - Clip Audio  (playout episode)
  - Guest 1 Audio / Guest 2 Audio  (contributor audio via guest_audio.py)
Per-input volume/mute comes from the backend: GET /api/mxl/audio-state
(same polling pattern as layout_pgm; UA header dodges the CF python rule).

Timestamp discipline (layout-saga lessons): every mxlsrc branch gets an
offset-locked normalizer onto THIS pipeline's clock; the audiomixer runs
with latency so an absent/starving branch becomes silence instead of a
stall; output keeps the v1 continuous restamp the encoder has been reading
happily for two days.

Guest flows may not exist yet (contributor offline): we build with whatever
exists and EXIT when a wanted flow appears/disappears — the supervisor
respawns us with the right set (brief program-audio blip, acceptable).
"""
import json
import os
import threading
import time
import urllib.request
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

STATE_URL = 'https://prodbots.com/api/mxl/audio-state'
DST = 'a0d10000-aaaa-4bbb-8ccc-000000000001'          # PGM Audio (encoder reads this)
CAPS = 'audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2,channel-mask=(bitmask)0x3'
MARGIN_NS = 66_000_000
SOURCES = {  # name -> audio flow uuid
    'playout': '4a37a1ae-e0e1-59de-8354-c6884b25e551',
    'guest1':  'a1111e00-aaaa-4bbb-8ccc-000000000001',
    'guest2':  'a2222e00-aaaa-4bbb-8ccc-000000000001',
}

Gst.init(None)

present = {n: os.path.isdir(f'/mxl-domain/{u}.mxl-flow') for n, u in SOURCES.items()}
built = [n for n, p in present.items() if p]

desc = (f'audiomixer name=mix latency=250000000 ! audioconvert ! {CAPS} ! '
        f'queue max-size-buffers=32 ! '
        f'mxlsink name=sink domain=/mxl-domain flow-id={DST} label="PGM Audio" '
        f'description="program audio mix" group-hint="Audio-PGM:Audio" sync=false '
        f'audiotestsrc wave=silence is-live=true ! audioconvert ! audioresample ! {CAPS} ! '
        f'queue ! mix.sink_0 ')
for i, n in enumerate(built):
    desc += (f'mxlsrc name=src_{n} domain=/mxl-domain audio-flow-id={SOURCES[n]} ! '
             f'audioconvert ! audioresample ! {CAPS} ! queue max-size-buffers=32 ! mix.sink_{i+1} ')

pipe = Gst.parse_launch(desc)
mix = pipe.get_by_name('mix')
sink = pipe.get_by_name('sink')
pads = {n: mix.get_static_pad(f'sink_{i+1}') for i, n in enumerate(built)}

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


def make_normalizer():
    st = {'off': None}

    def probe(pad, info):
        buf = info.get_buffer()
        clock = pipe.get_clock()
        if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        now = clock.get_time() - pipe.get_base_time()
        if st['off'] is None or abs((buf.pts + st['off']) - (now + MARGIN_NS)) > 500_000_000:
            st['off'] = now + MARGIN_NS - buf.pts
        buf.pts += st['off']
        return Gst.PadProbeReturn.OK
    return probe


for n in built:
    pipe.get_by_name(f'src_{n}').get_static_pad('src').add_probe(
        Gst.PadProbeType.BUFFER, make_normalizer())


def control():
    cur = None
    while True:
        try:
            req = urllib.request.Request(STATE_URL, headers={'User-Agent': 'mxl-audio/1.0'})
            with urllib.request.urlopen(req, timeout=3) as r:
                st = json.load(r)
            key = json.dumps(st, sort_keys=True)
            if key != cur:
                for n, pad in pads.items():
                    c = st.get(n, {})
                    pad.set_property('volume', 0.0 if c.get('mute') else max(0.0, min(2.0, c.get('vol', 100) / 100.0)))
                    pad.set_property('mute', bool(c.get('mute')))
                cur = key
                print(f'audio -> {st}', flush=True)
        except Exception:
            pass
        # rebuild when the set of existing source flows changes (guest joined/left)
        for n, u in SOURCES.items():
            if os.path.isdir(f'/mxl-domain/{u}.mxl-flow') != present[n]:
                print(f'audio source set changed ({n}) — exiting for rebuild', flush=True)
                os._exit(1)
        time.sleep(1)


def reattach_encoder():
    # our mxlsink just recreated the PGM Audio flow; the encoder's audio
    # reader wedges on recreated flows — ask the backend for a targeted
    # encoder-only bounce (server-side cooldown makes join/leave loops safe)
    time.sleep(3)
    try:
        req = urllib.request.Request('https://prodbots.com/api/mxl/audio-reattach',
                                     data=b'{}', headers={'Content-Type': 'application/json',
                                                          'User-Agent': 'mxl-audio/1.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f'audio-reattach -> {r.status}', flush=True)
    except Exception as e:
        print(f'audio-reattach failed: {e}', flush=True)


threading.Thread(target=control, daemon=True).start()
threading.Thread(target=reattach_encoder, daemon=True).start()
pipe.set_state(Gst.State.PLAYING)
print(f'audio_pgm mixer running (inputs: silence + {built})', flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (print(f'gst error: {m.parse_error()}', flush=True), loop.quit()))
bus.connect('message::eos', lambda b, m: loop.quit())
loop.run()
raise SystemExit(1)
