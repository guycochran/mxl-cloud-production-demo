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
DOMAIN = '/mxl-domain'

# Slots are DISCOVERED at runtime, never hardcoded — the probe renders whatever
# video sources actually exist on this facility (same discipline as the selector/
# healers in tools/mxl-flows.sh and the manifest emitter facility_from_discovery.py).
# Stale baked-in UUIDs are exactly what made the Health panel show dead slots for
# sources never present and miss the ones that were (reviewer R2). A background
# supervisor re-scans so sources that appear AFTER startup (a correspondent
# joining) get a worker, and a role whose flow is recreated with a new UUID has
# its worker re-attach to the live flow (fixes the ~2min slow re-attach wedge,
# HW Oct 10). Env MXL_THUMBS_SLOTS ("name=uuid,...") forces an explicit map.
#
# NOTE: kept INLINE (not imported) on purpose — run-mxl-thumbs.sh `docker cp`s
# this ONE file into the container, so it must be self-contained. The discovery
# helpers are module-level + the run loop is under __main__, so tests can import
# this module and call discover_slots() without starting any threads.

# PGM outputs, not source tiles — the multiview shows what you can CUT TO, and
# the selector/keyer are the program bus itself. (The 2-up/PiP composite 'layout'
# IS a wanted preview tile, so it is not excluded.)
EXCLUDE_ROLES = {'selector', 'keyer'}


def _grouphint_role(flow_def):
    """The role a flow belongs to, from its grouphint tag; None if untagged.
    "Guest1:Video" -> "guest1"; the never-interrupt stable flow collapses to its
    friendly name: "Guest5Stable:Video" -> "guest5" (operator sees "guest5")."""
    gh = flow_def.get('tags', {}).get('urn:x-nmos:tag:grouphint/v1.0') or []
    if gh:
        role = gh[0].split(':', 1)[0].strip().lower()
        if role.startswith('guest') and role.endswith('stable'):
            role = role[:-len('stable')]
        return role
    return None


def discover_slots(domain=DOMAIN, forced=None):
    """role-name -> video-flow UUID for every selectable VIDEO source present.
    forced: explicit "name=uuid,name=uuid" (from MXL_THUMBS_SLOTS) bypasses
    discovery for tests / odd facilities; None reads the env."""
    if forced is None:
        forced = os.environ.get('MXL_THUMBS_SLOTS', '').strip()
    if forced:
        out = {}
        for pair in forced.split(','):
            if '=' in pair:
                n, u = pair.split('=', 1)
                out[n.strip()] = u.strip()
        return out
    slots = {}
    try:
        entries = sorted(os.listdir(domain))
    except OSError:
        return slots
    for e in entries:
        if not e.endswith('.mxl-flow'):
            continue
        try:
            with open(f'{domain}/{e}/flow_def.json') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not str(d.get('format', '')).endswith(':video'):
            continue  # thumbnails are a video preview; skip audio/data flows
        role = _grouphint_role(d) or d.get('id', '')[:8]
        if role in EXCLUDE_ROLES:
            continue  # program outputs, not source tiles
        # For a never-interrupt slot GuestN and GuestNStable both collapse to
        # "guestN"; the STABLE flow is the one wired to the selector (what goes to
        # air), so it's the right tile. Stable sorts first by UUID, so first-wins
        # (setdefault) keeps it and ignores the volatile duplicate.
        slots.setdefault(role, d.get('id'))
    return slots


SLOTS = discover_slots(DOMAIN)

# per-slot overrides: the layout slot doubles as the live PREVIEW for the
# 2-up/PiP controls on mxl.html, so it renders faster and larger
FPS = {'layout': '2/1'}        # default 1/2 (one frame per 2s)
SIZE = {'layout': (480, 270)}  # default 320x180

# per-slot delivery state for health.json: 'last' = a frame arrived,
# 'changed' = the frame CONTENT changed (repeat-wedged readers keep 'last'
# fresh while 'changed' ages — the invisible wedge species, now visible)
STATE = {}


def worker(name, uuid):
    path = f'{OUT}/{name}.jpg'
    tmp = f'{OUT}/.{name}.tmp'
    while True:
        pipe = None
        # Re-read the role's CURRENT uuid each rebuild. The supervisor updates
        # SLOTS when a source reconnects under a new flow id; without this, a
        # wedged worker would keep re-attaching to the DEAD old uuid forever
        # (the ~2min "slow re-attach" wedge — HW Oct 10). Now a rebuild lands on
        # the live flow on the next cycle.
        uuid = SLOTS.get(name, uuid)
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
            try:
                ino0 = os.stat(f'/mxl-domain/{uuid}.mxl-flow').st_ino
            except OSError:
                ino0 = None
            while True:
                # flow recreated (guest reconnect/reset) -> our reader is on a
                # dead generation; rebuild NOW instead of waiting for the stall.
                try:
                    if ino0 is not None and os.stat(f'/mxl-domain/{uuid}.mxl-flow').st_ino != ino0:
                        raise RuntimeError('flow recreated — rebuilding reader')
                except OSError:
                    pass
                sample = sink.emit('try-pull-sample', 3 * Gst.SECOND)
                if sample is None:
                    if time.time() - last > 6:
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
                    elif time.time() - last_change > 10:
                        raise RuntimeError('content frozen 10s (repeat-wedged reader)')
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
        time.sleep(2)


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
            for name in list(SLOTS):  # snapshot: the supervisor may add roles concurrently
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


_running = set()  # roles that already have a worker thread


def _start_worker(name, uuid):
    _running.add(name)
    threading.Thread(target=worker, args=(name, uuid), daemon=True).start()


def supervisor():
    """Keep the worker set in sync with the live domain. Picks up sources that
    appear AFTER startup (a correspondent joining) and refreshes a role's UUID
    when its flow is recreated with a new id, so the worker re-attaches to the
    live flow instead of the dead one. Env MXL_THUMBS_SLOTS disables discovery
    (fixed map) — then there's nothing to re-scan."""
    if os.environ.get('MXL_THUMBS_SLOTS', '').strip():
        return
    while True:
        time.sleep(10)
        try:
            found = discover_slots(DOMAIN)
        except Exception as e:
            print(f'supervisor: {e}', flush=True)
            continue
        for name, uuid in found.items():
            if SLOTS.get(name) != uuid:
                SLOTS[name] = uuid  # worker re-reads this on its next rebuild
            if name not in _running:
                print(f'supervisor: new source {name} -> starting worker', flush=True)
                _start_worker(name, uuid)


if __name__ == '__main__':
    Gst.init(None)
    os.makedirs(OUT, exist_ok=True)
    for n, u in SLOTS.items():
        _start_worker(n, u)
    threading.Thread(target=health, daemon=True).start()
    threading.Thread(target=supervisor, daemon=True).start()
    print(f'mxl_thumbs running ({len(SLOTS)} slots discovered)', flush=True)
    while True:
        time.sleep(3600)
