#!/usr/bin/env python3
"""Multiview thumbnails for the MXL demo switcher (Tier 1).

One persistent low-rate pipeline per selector input: mxlsrc -> 1 frame every
2s -> 320x180 JPEG -> atomic write into /mxl-domain/thumbs/<name>.jpg (the
domain mount is the only host-visible dir this container has; a host
http.server on :8086 serves it to the backend's /api/mxl/thumbs proxy).

Self-healing per slot: missing flow (idle guest) or wedged reader (flow
recreated by a repair cascade -> try-pull stalls) tears the pipeline down,
deletes the stale JPEG so the UI shows a no-signal slate, and retries every
5s. v210 frames are just converted+scaled+jpeg'd - no decode, tiny CPU.
"""
import json
import os
import time
import threading
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

OUT = '/mxl-domain/thumbs'
SLOTS = {
    'cam':     'ca111e00-aaaa-4bbb-8ccc-000000000001',
    'playout': '2f34c189-64bf-5971-993a-332a28a7a6ee',
    'pattern': '6b5d8d68-64ce-56f8-bea2-e79b6c282a86',
    'cam2':    'ca222e00-aaaa-4bbb-8ccc-000000000001',
    'guest1':  '9e111e00-aaaa-4bbb-8ccc-000000000001',
    'guest2':  '9e222e00-aaaa-4bbb-8ccc-000000000001',
    'layout':  '1a900700-aaaa-4bbb-8ccc-000000000001',
}

# per-slot overrides: the layout slot doubles as the live PREVIEW for the
# 2-up/PiP controls on mxl.html, so it renders faster and larger
FPS = {'layout': '2/1'}        # default 1/2 (one frame per 2s)
SIZE = {'layout': (480, 270)}  # default 320x180

Gst.init(None)
os.makedirs(OUT, exist_ok=True)

# per-slot delivery state for health.json: 'last' = a frame arrived,
# 'changed' = the frame CONTENT changed (repeat-wedged readers keep 'last'
# fresh while 'changed' ages — the invisible wedge species, now visible)
STATE = {}


def worker(name, uuid):
    path = f'{OUT}/{name}.jpg'
    tmp = f'{OUT}/.{name}.tmp'
    while True:
        pipe = None
        try:
            fps = FPS.get(name, '1/2')
            w, h = SIZE.get(name, (320, 180))
            pipe = Gst.parse_launch(
                f'mxlsrc domain=/mxl-domain video-flow-id={uuid} ! queue ! '
                f'videorate drop-only=true ! video/x-raw,framerate={fps} ! '
                f'videoconvert ! videoscale ! video/x-raw,width={w},height={h} ! '
                f'jpegenc quality=70 ! appsink name=s max-buffers=1 drop=true sync=false')
            sink = pipe.get_by_name('s')
            pipe.set_state(Gst.State.PLAYING)
            last = time.time()
            last_sig, last_change = None, time.time()
            while True:
                sample = sink.emit('try-pull-sample', 3 * Gst.SECOND)
                if sample is None:
                    if time.time() - last > 12:
                        raise RuntimeError('stalled (wedged reader or writer gone)')
                    continue
                buf = sample.get_buffer()
                ok, mi = buf.map(Gst.MapFlags.READ)
                if ok:
                    data = bytes(mi.data)
                    buf.unmap(mi)
                    with open(tmp, 'wb') as f:
                        f.write(data)
                    os.replace(tmp, path)
                    last = time.time()
                    # a WEDGED reader repeats the same grain forever — buffers
                    # keep flowing so the stall check never fires. Every real
                    # source here has noise/timecode/motion, so byte-identical
                    # jpegs for 30s = wedged; rebuild for a fresh attach.
                    sig = data[-64:]
                    if sig != last_sig:
                        last_sig, last_change = sig, time.time()
                    elif time.time() - last_change > 30:
                        raise RuntimeError('content frozen 30s (repeat-wedged reader)')
                    STATE[name] = {'last': last, 'changed': last_change}
        except Exception as e:
            print(f'{name}: {e}', flush=True)
        finally:
            if pipe:
                pipe.set_state(Gst.State.NULL)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        STATE.pop(name, None)  # health.json shows the slot as no-signal
        time.sleep(5)


def health():
    """VM1 system health beside the thumbs -> rides the same :8086/tunnel/
    proxy path to the demo page (no new plumbing, no extra process).
    /proc/loadavg + /proc/meminfo are HOST-true inside the container."""
    path = os.path.join(OUT, 'health.json')
    while True:
        try:
            with open('/proc/loadavg') as f:
                l1, l5, l15 = f.read().split()[:3]
            mem = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    k, v = line.split(':', 1)
                    mem[k] = int(v.strip().split()[0])
            now = time.time()
            slots = {}
            for name in SLOTS:
                st = STATE.get(name)
                if st:
                    slots[name] = {'age': round(now - st['last'], 1),
                                   'frozen': round(now - st['changed'], 1)}
                else:
                    slots[name] = None  # no flow / worker rebuilding
            # which pipeline writers are alive in this container (pid ns =
            # container's, so this is exactly the demo's process set)
            procs = {}
            want = ('layout_pgm', 'audio_pgm', 'cam_ingest', 'cam2_ingest',
                    'guest_ingest', 'guest_audio')
            for pid in filter(str.isdigit, os.listdir('/proc')):
                try:
                    with open(f'/proc/{pid}/cmdline', 'rb') as f:
                        cmd = f.read().decode(errors='replace')
                except OSError:
                    continue
                for w in want:
                    if w in cmd:
                        procs[w] = procs.get(w, 0) + 1
            viewers = None
            try:  # written by the host-side mxl-viewer-count service
                with open(os.path.join(OUT, 'viewers.json')) as f:
                    viewers = json.load(f)
                if time.time() - viewers.get('ts', 0) > 120:
                    viewers = None  # counter down — don't show a stale zero
            except Exception:
                pass
            data = {
                'ts': int(now),
                'viewers': viewers,
                'load1': float(l1), 'load5': float(l5), 'load15': float(l15),
                'cores': os.cpu_count(),
                'mem_total_mb': mem.get('MemTotal', 0) // 1024,
                'mem_avail_mb': mem.get('MemAvailable', 0) // 1024,
                'swap_used_mb': (mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)) // 1024,
                'slots': slots,
                'procs': {w: procs.get(w, 0) for w in want},
            }
            tmp = path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except Exception as e:
            print(f'health: {e}', flush=True)
        time.sleep(15)


for n, u in SLOTS.items():
    threading.Thread(target=worker, args=(n, u), daemon=True).start()
threading.Thread(target=health, daemon=True).start()
print('mxl_thumbs running', flush=True)
while True:
    time.sleep(3600)
