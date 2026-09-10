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
}

Gst.init(None)
os.makedirs(OUT, exist_ok=True)


def worker(name, uuid):
    path = f'{OUT}/{name}.jpg'
    tmp = f'{OUT}/.{name}.tmp'
    while True:
        pipe = None
        try:
            pipe = Gst.parse_launch(
                f'mxlsrc domain=/mxl-domain video-flow-id={uuid} ! queue ! '
                f'videorate drop-only=true ! video/x-raw,framerate=1/2 ! '
                f'videoconvert ! videoscale ! video/x-raw,width=320,height=180 ! '
                f'jpegenc quality=70 ! appsink name=s max-buffers=1 drop=true sync=false')
            sink = pipe.get_by_name('s')
            pipe.set_state(Gst.State.PLAYING)
            last = time.time()
            while True:
                sample = sink.emit('try-pull-sample', 3 * Gst.SECOND)
                if sample is None:
                    if time.time() - last > 12:
                        raise RuntimeError('stalled (wedged reader or writer gone)')
                    continue
                buf = sample.get_buffer()
                ok, mi = buf.map(Gst.MapFlags.READ)
                if ok:
                    with open(tmp, 'wb') as f:
                        f.write(mi.data)
                    buf.unmap(mi)
                    os.replace(tmp, path)
                    last = time.time()
        except Exception as e:
            print(f'{name}: {e}', flush=True)
        finally:
            if pipe:
                pipe.set_state(Gst.State.NULL)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        time.sleep(5)


for n, u in SLOTS.items():
    threading.Thread(target=worker, args=(n, u), daemon=True).start()
print('mxl_thumbs running', flush=True)
while True:
    time.sleep(3600)
