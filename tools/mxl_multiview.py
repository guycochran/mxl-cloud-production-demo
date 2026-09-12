#!/usr/bin/env python3
"""MXL-native multiview compositor (Tier 1.5): 8 domain flows -> one 3x3 wall.

All seven switcher inputs plus the Keyer PGM are read straight out of the
MXL shared-memory domain (mxlsrc), downscaled to 640x360, composited on the
CPU into a 1920x1080 v210 canvas at 15fps, and written back to the domain as
a new "Multiview PGM" flow (mxlsink). No decode of any contribution stream,
no GPU. mv_encode.py (mxl2webrtc container) encodes this ONE flow for
browsers — the popout at prodbots.com/mxl-multiview.html.

Grid (row-major, 640x360 tiles; bottom-right stays black):
    cam     cam2    playout
    guest1  guest2  pattern
    layout  PGM     (black)

Deliberately dumber than layout_pgm.py: no selectors, no valves, no fades,
no control polling — geometry is static, so nothing ever rebuilds. The
patterns that ARE copied from layout_pgm are the ones paid for in outages:
per-input offset-locked normalizers (not per-buffer "now" stamps), the
slewing output servo with the 8-relocks/10s escape hatch, and
branch-error-silencing so one dead source blacks its tile instead of
killing the wall. A branch whose writer is LIVE but silent >20s (flow
recreated under us — e.g. Keyer PGM after a repair cascade) exits for a
clean respawn: this flow is never a selector input, so a respawn is
invisible except ~10s of frozen wall.
"""
import json
import threading
import time
import urllib.request
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

DST = 'ab900700-aaaa-4bbb-8ccc-000000000001'   # Multiview PGM
FPS = 30                                        # wall cadence (30 tried 9/12: watch load vs the
                                                # babysitter's MAX_LOAD=16 guard; 15 = the cheap fallback)
SLOTS = ['cam', 'cam2', 'playout', 'guest1', 'guest2', 'pattern', 'layout', 'pgm']
FLOWS = {
    'cam':     'ca111e00-aaaa-4bbb-8ccc-000000000001',
    'cam2':    'ca222e00-aaaa-4bbb-8ccc-000000000001',
    'playout': '2f34c189-64bf-5971-993a-332a28a7a6ee',
    'guest1':  '9e111e00-aaaa-4bbb-8ccc-000000000001',
    'guest2':  '9e222e00-aaaa-4bbb-8ccc-000000000001',
    'pattern': '6b5d8d68-64ce-56f8-bea2-e79b6c282a86',
    'layout':  '1a900700-aaaa-4bbb-8ccc-000000000001',
    'pgm':     '5c73394e-85df-50a3-8988-5edde5b5522a',
}
POS = [(0, 0), (640, 0), (1280, 0),
       (0, 360), (640, 360), (1280, 360),
       (0, 720), (640, 720)]
MARGIN_NS = 66_000_000
FRAME_NS = Gst.SECOND // FPS

Gst.init(None)

branches = ''
pads = ''
for i, name in enumerate(SLOTS):
    x, y = POS[i]
    branches += (
        f'mxlsrc name=src{i} domain=/mxl-domain video-flow-id={FLOWS[name]} '
        f'! queue max-size-buffers=8 leaky=downstream '
        f'! videorate ! video/x-raw,framerate={FPS}/1 '
        f'! videoconvert n-threads=1 ! videoscale '
        f'! video/x-raw,width=640,height=360 '
        f'! queue max-size-buffers=8 ! comp.sink_{i} ')
    pads += f'sink_{i}::xpos={x} sink_{i}::ypos={y} sink_{i}::zorder={i+1} '

pipe = Gst.parse_launch(
    branches +
    # NO ignore-inactive-pads: at startup every pad is inactive, so the
    # aggregator EOS'd instantly (silent exit-1 crash loop, 9/12). The
    # latency timeout alone handles absent sources — layout_pgm's proof.
    f'compositor name=comp background=black latency=200000000 {pads}! '
    f'video/x-raw,width=1920,height=1080 ! '
    f'videorate drop-only=false ! video/x-raw,framerate={FPS}/1 ! videoconvert ! '
    f'video/x-raw,format=v210 ! queue max-size-buffers=8 ! '
    f'mxlsink name=sink domain=/mxl-domain flow-id={DST} label="Multiview PGM" '
    f'description="3x3 multiview wall of all switcher inputs + program" '
    f'group-hint="Multiview:Video" sync=false')

sink = pipe.get_by_name('sink')

# Output restamp — layout_pgm's v4 slewing servo verbatim (see its comment
# block for the history: free-running counters, thresholds and per-buffer
# "now" stamps all failed in production).
out_state = {'off': None}
SLEW_MAX_NS = 80_000
relock = {'times': []}


def out_restamp(pad, info):
    buf = info.get_buffer()
    clock = pipe.get_clock()
    if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
        return Gst.PadProbeReturn.OK
    now = clock.get_time() - pipe.get_base_time()
    if out_state['off'] is None:
        out_state['off'] = now + MARGIN_NS - buf.pts
    mapped = buf.pts + out_state['off']
    err = mapped - (now + MARGIN_NS)
    if abs(err) > Gst.SECOND:
        out_state['off'] = now + MARGIN_NS - buf.pts
        mapped = now + MARGIN_NS
        print(f'output hard re-lock ({err/1e9:+.2f}s)', flush=True)
        wall = time.monotonic()
        relock['times'] = [t for t in relock['times'] if wall - t < 10] + [wall]
        if len(relock['times']) >= 8:
            print('output re-lock LOOP (8/10s) — exiting for fresh offset', flush=True)
            import os
            os._exit(1)
    else:
        corr = max(-SLEW_MAX_NS, min(SLEW_MAX_NS, int(err * 0.02)))
        out_state['off'] -= corr
    buf.pts = mapped
    buf.duration = FRAME_NS
    return Gst.PadProbeReturn.OK


sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, out_restamp)

# Per-input normalizer — one locked offset per source (re-locked on >500ms
# drift); per-buffer "now" stamps make videorate drop/duplicate (frozen-pan bug).
last_buf = {i: 0.0 for i in range(len(SLOTS))}


def make_normalizer(idx):
    st = {'off': None}

    def probe(pad, info):
        buf = info.get_buffer()
        last_buf[idx] = time.monotonic()
        clock = pipe.get_clock()
        if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        now = clock.get_time() - pipe.get_base_time()
        if st['off'] is None or abs((buf.pts + st['off']) - (now + MARGIN_NS)) > 500_000_000:
            st['off'] = now + MARGIN_NS - buf.pts
        buf.pts += st['off']
        return Gst.PadProbeReturn.OK
    return probe


for _i in range(len(SLOTS)):
    pipe.get_by_name(f'src{_i}').get_static_pad('src') \
        .add_probe(Gst.PadProbeType.BUFFER, make_normalizer(_i))


def wedge_watch():
    """A branch silent >20s while its writer is alive = our reader wedged on
    a flow recreation (Keyer PGM after every repair cascade is the usual
    one). This wall is not a selector input, so a respawn costs nothing on
    air — exit and let the supervisor give us fresh readers. Guests with no
    contributor are silent legitimately (status API live=false) — never
    respawn for those. PGM isn't in the status slots; treat it as always
    expected while ANY other branch flows."""
    time.sleep(20)  # first frames: cam needs ~3s, don't false-fire at boot
    while True:
        time.sleep(3)
        now = time.monotonic()
        flowing = [i for i, t in last_buf.items() if now - t < 5]
        if not flowing:
            continue  # global stall is a different problem
        silent = [i for i, t in last_buf.items() if now - t > 20]
        if not silent:
            continue
        try:
            req = urllib.request.Request('https://prodbots.com/api/mxl/status',
                                         headers={'User-Agent': 'mxl-multiview/1.0'})
            live = {sl['name']: sl['live'] for sl in
                    json.load(urllib.request.urlopen(req, timeout=5)).get('slots', [])}
        except Exception:
            continue  # can't verify — never respawn blind
        wedged = [SLOTS[i] for i in silent
                  if SLOTS[i] == 'pgm' or live.get(SLOTS[i])]
        if wedged:
            print(f'wedge: {wedged} silent >20s with live writer(s) — '
                  f'exiting for fresh attach', flush=True)
            import os
            os._exit(1)


threading.Thread(target=wedge_watch, daemon=True).start()

pipe.set_state(Gst.State.PLAYING)
print('mxl_multiview running', flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()

# One bad source blacks its tile; only sink/compositor errors (or a mass
# die-off) take the process down — layout_pgm's wedge-storm lesson.
err_state = {'dead': set(), 'times': []}
SRC_NAMES = {f'src{i}': i for i in range(len(SLOTS))}


def on_error(b, m):
    err, dbg = m.parse_error()
    name = m.src.get_name() if m.src else '?'
    print(f'gst error from {name}: {err}', flush=True)
    branch = SRC_NAMES.get(name)
    if branch is None:
        for sn, idx in SRC_NAMES.items():
            if name.startswith(sn):
                branch = idx
                break
    now = time.monotonic()
    err_state['times'] = [t for t in err_state['times'] if now - t < 20] + [now]
    if branch is not None and len(err_state['dead']) < len(SLOTS) - 1 and len(err_state['times']) < 12:
        err_state['dead'].add(branch)
        print(f'  silencing branch {SLOTS[branch]} — wall stays alive on the rest', flush=True)
        try:
            pipe.get_by_name(f'src{branch}').set_state(Gst.State.NULL)
        except Exception as e:
            print(f'  could not silence {SLOTS[branch]}: {e}', flush=True)
        return
    print('  fatal (sink/compositor) or too many dead branches — exiting for clean respawn', flush=True)
    loop.quit()


bus.connect('message::error', on_error)
bus.connect('message::eos', lambda b, m: (print('EOS on bus — exiting', flush=True), loop.quit()))
loop.run()
raise SystemExit(1)
