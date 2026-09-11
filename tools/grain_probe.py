#!/usr/bin/env python3
"""Grain-rate + uniqueness probe for the MXL demo health board (v2).

Why uniqueness and not just rate: every wedge species we've hit passes the
rate checks. A repeat-wedged reader delivers 30 buffers/s of the SAME frame
— head advances, grains tick, thumbnails refresh — and the only measurable
symptom is unique-frames/sec collapsing. (Found live 9/11: guest1 juddering
on program while every hop measured a clean 30fps; the selector's stale
reader was repeating frames at full rate.)

v2: PERSISTENT readers, one per flow, in a single pipeline — v1 attached a
fresh reader per sweep, and fresh attaches to the keyer flow deliver ~1/3
repeats while the encoder's long-lived reader is perfectly healthy (the
stale-attach artifact defaming a healthy keyer; proven with a browser-side
timecode-region measure reading 29.4 true fps). Persistent readers see what
the production readers see. Hashes CENTER scanlines (first 4KB of 1080p
v210 is under one scanline — letterboxed/keyed flows have static top rows).

Writes /mxl-domain/thumbs/grains.json every 3s with a rolling 3s window:
{name: {bps, ufps} | null}. Self-heals: if a flow reads nothing for >90s
the whole probe exits (runner respawns -> fresh attaches) — probes must
never be the thing that stays wedged.
"""
import hashlib
import json
import os
import threading
import time
from collections import deque
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

FLOWS = {
    'cam':      'ca111e00-aaaa-4bbb-8ccc-000000000001',
    'playout':  '2f34c189-64bf-5971-993a-332a28a7a6ee',
    'pattern':  '6b5d8d68-64ce-56f8-bea2-e79b6c282a86',
    'cam2':     'ca222e00-aaaa-4bbb-8ccc-000000000001',
    'guest1':   '9e111e00-aaaa-4bbb-8ccc-000000000001',
    'guest2':   '9e222e00-aaaa-4bbb-8ccc-000000000001',
    'layout':   '1a900700-aaaa-4bbb-8ccc-000000000001',
    'selector': '9437652d-20d9-565e-be6e-b98c36067930',
    'keyer':    '5c73394e-85df-50a3-8988-5edde5b5522a',
}
OUT = '/mxl-domain/thumbs/grains.json'
WINDOW_S = 3.0
STALL_EXIT_S = 90     # all-flows-silent this long -> exit for fresh attaches

samples = {n: deque(maxlen=200) for n in FLOWS}   # (monotonic_ts, hash)
pipes = {}


def load1():
    with open('/proc/loadavg') as f:
        return float(f.read().split()[0])


# Load guard — the probe must never be what tips the box over (v2 helped
# shove VM1 to load 24 and the selector API starved). Don't even attach
# readers while the box is busy; bail out if load climbs while running.
while load1() > 17.5:
    print(f'load {load1():.1f} > 17.5 — waiting to start', flush=True)
    time.sleep(30)


def reader(name, uuid):
    try:
        pipe = Gst.parse_launch(
            f'mxlsrc domain=/mxl-domain video-flow-id={uuid} '
            f'! appsink name=s max-buffers=4 drop=true sync=false')
        pipes[name] = pipe
        sink = pipe.get_by_name('s')
        pipe.set_state(Gst.State.PLAYING)
        while True:
            sm = sink.emit('try-pull-sample', 500 * Gst.MSECOND)
            if sm is None:
                continue
            buf = sm.get_buffer()
            # extract_dup copies ONLY the 8KB slice. v2 used map + mi.data,
            # and PyGI materializes the WHOLE 8MB v210 buffer as bytes —
            # ~2GB/s of copying across 9 readers, 126% CPU, and it shoved a
            # loaded VM1 to load 24 (selector API starved). Center scanlines,
            # same reasoning as before.
            sz = buf.get_size()
            chunk = buf.extract_dup(sz // 2, min(8192, sz // 2))
            h = hashlib.md5(chunk).hexdigest()
            samples[name].append((time.monotonic(), h))
    except Exception as e:
        print(f'reader {name}: {e}', flush=True)


for n, u in FLOWS.items():
    threading.Thread(target=reader, args=(n, u), daemon=True).start()

last_any = time.monotonic()
while True:
    time.sleep(3)
    now = time.monotonic()
    sweep = {}
    for n in FLOWS:
        win = [(t, h) for t, h in samples[n] if now - t <= WINDOW_S]
        if not win:
            sweep[n] = None
        else:
            last_any = now
            sweep[n] = {'bps': round(len(win) / WINDOW_S, 1),
                        'ufps': round(len(set(h for _, h in win)) / WINDOW_S, 1)}
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'ts': time.time(), 'flows': sweep}, f)
    os.replace(tmp, OUT)
    if now - last_any > STALL_EXIT_S:
        print('all flows silent — exiting for fresh attaches', flush=True)
        os._exit(1)
    if load1() > 19.5:
        print(f'load {load1():.1f} > 19.5 — exiting to shed weight (runner respawns when calm)', flush=True)
        os._exit(1)
