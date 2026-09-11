#!/usr/bin/env python3
"""Program layout compositor for the MXL demo switcher (Tier 3: 2-up / PiP).

Writes a "Layout PGM" flow that composites TWO of the six switcher inputs.
It is a normal selector input (slot 6) — going to a layout is just a cut,
so PVW/CUT and the keyer work unchanged.

The pipeline holds ALL SIX sources behind two input-selectors feeding a
compositor, so every layout operation is a live property flip:
  - change source A/B  -> input-selector active-pad swap (instant)
  - 2up <-> pip        -> compositor pad geometry props   (instant)
Nothing is rebuilt, the output flow is never recreated, and therefore the
video selector's reader on slot 6 can never wedge from a layout change.

Control: polls the demo backend (GET /api/mxl/layout-state, outbound HTTPS —
same pattern as guest_ingest's self-announce) every 500ms:
{"style":"2up"|"pip","a":"cam","b":"guest1"}

Output cadence: continuous 30fps PTS locked to now+2 grains (same restamp
pattern as cam_ingest/audio_pgm) so the layout is instantly cuttable.
"""
import json
import threading
import time
import urllib.request
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

CMD_URL = 'https://prodbots.com/api/mxl/layout-state'
INPUT_URL = 'https://prodbots.com/api/mxl/input'      # fade choreography cuts
DONE_URL = 'https://prodbots.com/api/mxl/fade-done'
DST = '1a900700-aaaa-4bbb-8ccc-000000000001'   # Layout PGM
SLOTS = ['cam', 'playout', 'pattern', 'cam2', 'guest1', 'guest2']
FLOWS = {
    'cam':     'ca111e00-aaaa-4bbb-8ccc-000000000001',
    'playout': '2f34c189-64bf-5971-993a-332a28a7a6ee',
    'pattern': '6b5d8d68-64ce-56f8-bea2-e79b6c282a86',
    'cam2':    'ca222e00-aaaa-4bbb-8ccc-000000000001',
    'guest1':  '9e111e00-aaaa-4bbb-8ccc-000000000001',
    'guest2':  '9e222e00-aaaa-4bbb-8ccc-000000000001',
}
MARGIN_NS = 66_000_000
FRAME_NS = Gst.SECOND // 30

Gst.init(None)

branches = ''
for i, name in enumerate(SLOTS):
    branches += (
        f'mxlsrc name=src{i} domain=/mxl-domain video-flow-id={FLOWS[name]} ! queue max-size-buffers=8 leaky=downstream ! tee name=t{i} '
        f't{i}. ! queue max-size-buffers=8 leaky=downstream ! selA.sink_{i} '
        f't{i}. ! queue max-size-buffers=8 leaky=downstream ! selB.sink_{i} '
    )

pipe = Gst.parse_launch(
    branches +
    f'input-selector name=selA sync-mode=1 ! videorate ! video/x-raw,framerate=30/1 ! videoconvert n-threads=2 ! videoscale ! capsfilter name=fcapsA ! queue max-size-buffers=8 ! comp.sink_0 '
    f'input-selector name=selB sync-mode=1 ! videorate ! video/x-raw,framerate=30/1 ! videoconvert n-threads=2 ! videoscale ! capsfilter name=fcapsB ! queue max-size-buffers=8 ! comp.sink_1 '
    f'compositor name=comp background=black latency=200000000 ! video/x-raw,width=1920,height=1080 ! '
    f'videorate drop-only=false ! video/x-raw,framerate=30/1 ! videoconvert ! '
    f'video/x-raw,format=v210 ! queue max-size-buffers=8 ! '
    f'mxlsink name=sink domain=/mxl-domain flow-id={DST} label="Layout PGM" '
    f'description="2-up / PiP composite of two switcher inputs" '
    f'group-hint="Layout:Video" sync=false')

selA = pipe.get_by_name('selA')
selB = pipe.get_by_name('selB')
comp = pipe.get_by_name('comp')
sink = pipe.get_by_name('sink')
padA = comp.get_static_pad('sink_0')
padB = comp.get_static_pad('sink_1')
fcapsA = pipe.get_by_name('fcapsA')
fcapsB = pipe.get_by_name('fcapsB')

# Output restamp v4 — a slow SERVO, not a threshold. History: a free-running
# counter scattered ring indices (frozen picture); no restamp let the grid
# drift unbounded (~25 min -> "grain too early" wedge); a 150ms-threshold
# re-lock (v3) fired constantly because bursty delivery has ±300ms PHASE
# WOBBLE without net drift — each re-lock was a visible jump. v4 follows the
# buffer timeline through one offset and slews that offset toward
# (now + margin) by at most 80µs per frame: true clock drift (~1ms/s scale)
# is absorbed invisibly, wobble barely moves it, and there are no jumps.
# (mxlsink PTS = ring ADDRESS in pipeline running time — the 1-grain-stall
# diag proved raw domain timestamps block the sink forever.)
out_state = {'off': None}
SLEW_MAX_NS = 80_000  # per frame; 2.4ms/s of correction capacity


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
        # catastrophic (startup transient / stall recovery): hard re-lock —
        # one visible discontinuity beats minutes of stale ring writes
        out_state['off'] = now + MARGIN_NS - buf.pts
        mapped = now + MARGIN_NS
        print(f'output hard re-lock ({err/1e9:+.2f}s)', flush=True)
    else:
        corr = max(-SLEW_MAX_NS, min(SLEW_MAX_NS, int(err * 0.02)))
        out_state['off'] -= corr
    buf.pts = mapped
    buf.duration = FRAME_NS
    return Gst.PadProbeReturn.OK


sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, out_restamp)


# Input normalizer — cam_relay's latency-normalizer pattern: domain-grain
# PTS mean nothing to this pipeline's clock, but their CADENCE is perfect
# (every writer restamps continuously). So shift each source by ONE locked
# offset (re-locked on >500ms drift) instead of stamping "now" per buffer:
# now-based stamps jitter with arrival and make the per-branch videorate
# drop real frames and fill with duplicates (the frozen-pan bug).
last_buf = {i: 0.0 for i in range(len(SLOTS))}  # monotonic ts of last buffer per source


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


def apply_geometry(style):
    # scaling happens in the per-branch videoscale elements (parallel threads,
    # set via the fcaps filters) — the compositor's own pad width/height
    # scaling ran inside its single aggregation thread and capped the whole
    # pipeline at ~28.5fps (the diag graphs skipped geometry, which is why
    # they always measured a perfect 30). Pads here do POSITION ONLY.
    if style == 'pip':
        fcapsA.set_property('caps', Gst.Caps.from_string('video/x-raw,width=1920,height=1080'))
        fcapsB.set_property('caps', Gst.Caps.from_string('video/x-raw,width=560,height=316'))
        padA.set_property('xpos', 0);    padA.set_property('ypos', 0)
        padB.set_property('xpos', 1320); padB.set_property('ypos', 724)
        padA.set_property('zorder', 1);  padB.set_property('zorder', 2)
    else:  # 2up side-by-side, vertically centred
        fcapsA.set_property('caps', Gst.Caps.from_string('video/x-raw,width=960,height=540'))
        fcapsB.set_property('caps', Gst.Caps.from_string('video/x-raw,width=960,height=540'))
        padA.set_property('xpos', 0);    padA.set_property('ypos', 270)
        padB.set_property('xpos', 960);  padB.set_property('ypos', 270)
        padA.set_property('zorder', 1);  padB.set_property('zorder', 2)


def _post(url, obj):
    try:
        req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                     headers={'Content-Type': 'application/json',
                                              'User-Agent': 'mxl-layout/1.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)
    except Exception as e:
        print(f'post {url}: {e}', flush=True)
        return None


# AUTO transition (fade) — this compositor doubles as the switcher's
# transition M/E. The backend parks a {id, from, to, frames} command in
# layout-state; we run the whole choreography: both selectors fullscreen
# (A=outgoing visible, B=incoming alpha 0), an invisible selector cut to
# slot 6, the alpha ramp (the actual on-air mix), an invisible cut to the
# target, then hand the pads back to the user's 2-up/PiP.
fade_st = {'busy': False, 'seen': 0}


def run_fade(f, ctl):
    ia, ib, n = int(f['from']), int(f['to']), max(2, min(150, int(f.get('frames', 20))))
    try:
        full = Gst.Caps.from_string('video/x-raw,width=1920,height=1080')
        selA.set_property('active-pad', selA.get_static_pad(f'sink_{ia}'))
        selB.set_property('active-pad', selB.get_static_pad(f'sink_{ib}'))
        active['a'] = ia
        active['b'] = ib
        fcapsA.set_property('caps', full)
        fcapsB.set_property('caps', full)
        for p in (padA, padB):
            p.set_property('xpos', 0)
            p.set_property('ypos', 0)
        padA.set_property('zorder', 1)
        padA.set_property('alpha', 1.0)
        padB.set_property('zorder', 2)
        padB.set_property('alpha', 0.0)
        time.sleep(0.4)  # let both branches roll fullscreen before the hidden cut
        _post(INPUT_URL, {'input': 6, '_fade': f['id']})
        time.sleep(0.25)
        for s in range(1, n + 1):
            padB.set_property('alpha', s / n)
            time.sleep(1 / 30)
        _post(INPUT_URL, {'input': ib, '_fade': f['id']})
        time.sleep(0.25)
        print(f'fade {SLOTS[ia]} -> {SLOTS[ib]} ({n}f) done', flush=True)
    except Exception as e:
        print(f'fade: {e}', flush=True)
    finally:
        padA.set_property('alpha', 1.0)
        padB.set_property('alpha', 1.0)
        ctl['cur'] = None          # force control() to restore the user layout
        fade_st['busy'] = False
        _post(DONE_URL, {'id': f['id']})


def control():
    ctl = {'cur': None}
    while True:
        try:
            # UA header dodges the zone's python-urllib bot rule (CF-1010,
            # same fix as the TAMS shipper)
            req = urllib.request.Request(CMD_URL, headers={'User-Agent': 'mxl-layout/1.0'})
            with urllib.request.urlopen(req, timeout=3) as r:
                cmd = json.load(r)
            fade = cmd.get('fade')
            if fade and fade.get('id') != fade_st['seen'] and not fade_st['busy'] \
                    and 0 <= fade.get('from', -1) <= 5 and 0 <= fade.get('to', -1) <= 5:
                fade_st['seen'] = fade['id']
                fade_st['busy'] = True
                threading.Thread(target=run_fade, args=(fade, ctl), daemon=True).start()
            if fade_st['busy']:
                time.sleep(0.3)   # pads belong to the fade; poll fast, apply nothing
                continue
            key = (cmd.get('style', '2up'), cmd.get('a', 'cam'), cmd.get('b', 'cam2'))
            if key != ctl['cur']:
                style, a, b = key
                ia = SLOTS.index(a) if a in SLOTS else 0
                ib = SLOTS.index(b) if b in SLOTS else 3
                selA.set_property('active-pad', selA.get_static_pad(f'sink_{ia}'))
                selB.set_property('active-pad', selB.get_static_pad(f'sink_{ib}'))
                active['a'] = ia
                active['b'] = ib
                apply_geometry(style)
                ctl['cur'] = key
                print(f'layout -> {style} A={a} B={b}', flush=True)
        except Exception:
            pass  # backend briefly unreachable — keep last layout
        time.sleep(0.5)  # also the AUTO trigger latency — keep snappy


apply_geometry('2up')
selA.set_property('active-pad', selA.get_static_pad('sink_0'))
selB.set_property('active-pad', selB.get_static_pad('sink_3'))
active = {'a': 0, 'b': 3}  # kept current by control()


def wedge_watch():
    """An MXL reader wedges silently when its source flow is recreated
    (writer restart) after we attached — the branch stops delivering while
    everything else flows. Repairing the selector doesn't help US; only a
    fresh attach does. If a SELECTED branch goes silent >8s while at least
    one other branch still flows, exit: the supervisor respawns us with
    fresh readers, and the pre-exit repair announce re-attaches slot 6."""
    import urllib.request as _rq
    time.sleep(15)  # let startup settle
    while True:
        time.sleep(2)
        now = time.monotonic()
        flowing = [i for i, t in last_buf.items() if now - t < 4]
        if not flowing:
            continue  # global stall = different problem, not a per-branch wedge
        for key in ('a', 'b'):
            i = active[key]
            if last_buf[i] > 0 and now - last_buf[i] > 8:
                print(f'input wedge: selected source {SLOTS[i]} silent '
                      f'{now - last_buf[i]:.0f}s while others flow — exiting for fresh attach', flush=True)
                try:
                    req = _rq.Request('https://prodbots.com/api/mxl/repair',
                                      data=b'{"auto":1}',
                                      headers={'Content-Type': 'application/json',
                                               'User-Agent': 'mxl-layout/1.0'})
                    _rq.urlopen(req, timeout=8)
                except Exception:
                    pass
                import os
                os._exit(1)


threading.Thread(target=control, daemon=True).start()
threading.Thread(target=wedge_watch, daemon=True).start()

pipe.set_state(Gst.State.PLAYING)
print('layout_pgm running', flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()
bus.connect('message::error', lambda b, m: (print(f'gst error: {m.parse_error()}', flush=True), loop.quit()))
bus.connect('message::eos', lambda b, m: loop.quit())
loop.run()
raise SystemExit(1)
