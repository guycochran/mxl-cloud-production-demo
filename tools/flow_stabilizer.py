#!/usr/bin/env python3
"""Flow stabilizer — the first-principles fix for the reader-wedge class.

A real switcher's Super Source never has a dead pane because its crossbar
inputs are always hot. Ours died whenever a source flow was RECREATED
(guest reconnects, fabric-leg heals): every downstream mxlsrc reader
wedges permanently on recreation (MXL v1.1.0), and all of this week's
repair cascades / take-checks / escalations were scar tissue around that.

This tool makes recreation invisible. It reads a VOLATILE flow and writes
a STABLE flow that is created once at boot and never recreated. When the
volatile side goes silent (writer died / flow recreated), it swaps ONLY
its reader element in-place — sink and stable flow keep running — so
downstream consumers (selector slot, layout panes, thumbs, probe) point
at the stable flow and can never wedge again. Same architecture role as
the proven cam relay, plus in-place reader swap instead of process death.

Usage: flow_stabilizer.py <name> <volatile-flow-uuid> <stable-flow-uuid> [label]

Cadence: output restamped to now+MARGIN with the cam_relay offset-lock
pattern (domain PTS = ring addresses in OUR running time; the swapped-in
reader's timestamps mean nothing downstream).
"""
import sys
import threading
import time
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

NAME = sys.argv[1] if len(sys.argv) > 1 else 'guest1'
SRC_UUID = sys.argv[2] if len(sys.argv) > 2 else '9e111e00-aaaa-4bbb-8ccc-000000000001'
DST_UUID = sys.argv[3] if len(sys.argv) > 3 else '57ab1e00-aaaa-4bbb-8ccc-000000000001'
LABEL = sys.argv[4] if len(sys.argv) > 4 else f'{NAME} stable'
DOMAIN = '/mxl-domain'
MARGIN_NS = 66_000_000
SILENT_SWAP_S = 6      # volatile side quiet this long -> swap the reader
SWAP_COOLDOWN_S = 15   # don't thrash swaps while a guest is simply absent

Gst.init(None)

FRAME_NS = Gst.SECOND * 1001 // 30000 if False else Gst.SECOND // 30
FREEWHEEL_S = 20   # bridge input gaps (heals/respawns) this long, then go dark

# INPUT side: replaceable reader -> appsink (latest-frame mailbox)
inpipe = Gst.parse_launch(
    f'mxlsrc name=reader domain={DOMAIN} video-flow-id={SRC_UUID} '
    f'! queue name=q max-size-buffers=4 leaky=downstream '
    f'! appsink name=asink max-buffers=2 drop=true sync=false')
q = inpipe.get_by_name('q')
asink = inpipe.get_by_name('asink')

# OUTPUT side: SEPARATE pipeline, free-running 30fps clock -> mxlsink.
# The stable flow's timeline is OURS: perfectly continuous, never stalls,
# never jumps — a downstream reader can never see a discontinuity (the 9/12
# on-air kill test froze program because a passthrough stable flow stalled
# ~15s and timeline-jumped when the layout engine respawned).
outpipe = Gst.parse_launch(
    f'appsrc name=asrc is-live=true format=time do-timestamp=false '
    f'! mxlsink name=sink domain={DOMAIN} flow-id={DST_UUID} '
    f'label="{LABEL}" description="stabilized {NAME} (freewheeling conform)" '
    f'group-hint="{NAME.capitalize()}Stable:Video" sync=false')
asrc = outpipe.get_by_name('asrc')

state = {'latest': None, 'latest_at': 0.0, 'caps_set': False,
         'n': 0, 'base': None, 'pushed': 0, 't0': None}


def pull_loop():
    while True:
        sm = asink.emit('try-pull-sample', 500 * Gst.MSECOND)
        if sm is None:
            continue
        state['latest'] = sm          # keep the whole sample (buffer + caps)
        state['latest_at'] = time.monotonic()
        state['dry_swaps'] = 0
        state['n'] += 1
        if state['n'] % 300 == 0:
            print(f'diag in n={state["n"]}', flush=True)


def push_loop():
    # fixed 33.33ms grid stamped against OUR clock with a slow servo
    # (layout_pgm's v4 restamp lesson: slew, don't jump). Pacing is by
    # ABSOLUTE deadline — a fudge-factor sleep (v3) ran 8% fast, the servo
    # couldn't absorb it, and the output hard-re-locked every ~13s: each
    # re-lock is a timestamp jump on the stable flow = the wedge class
    # sneaking back in through this very tool.
    next_at = time.monotonic()
    while True:
        sm = state['latest']
        now_m = time.monotonic()
        if sm is not None and now_m - state['latest_at'] <= FREEWHEEL_S:
            clock = outpipe.get_clock()
            if clock:
                now = clock.get_time() - outpipe.get_base_time()
                if state['base'] is None:
                    state['base'] = now + MARGIN_NS
                    state['pushed'] = 0
                pts = state['base'] + state['pushed'] * FRAME_NS
                err = pts - (now + MARGIN_NS)
                if abs(err) > Gst.SECOND:
                    state['base'] = now + MARGIN_NS - state['pushed'] * FRAME_NS
                    pts = now + MARGIN_NS
                    print('output hard re-lock', flush=True)
                else:
                    state['base'] -= max(-80_000, min(80_000, int(err * 0.02)))
                if not state['caps_set']:
                    asrc.set_property('caps', sm.get_caps())
                    state['caps_set'] = True
                    print(f'caps set: {sm.get_caps().to_string()[:70]}', flush=True)
                buf = sm.get_buffer().copy()
                buf.pts = pts
                buf.dts = pts
                buf.duration = FRAME_NS
                asrc.emit('push-buffer', buf)
                state['pushed'] += 1
        else:
            # no input (idle guest / gap expired): stop pushing so the flow
            # head goes stale and the UI correctly shows 'awaiting feed'
            state['base'] = None
        next_at += FRAME_NS / 1e9
        delay = next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -1.0:
            next_at = time.monotonic()  # fell far behind (suspend) — resync


def swap_reader():
    """Replace the mxlsrc in-place on the INPUT pipeline. The output
    pipeline (and the stable flow) never stops. This is the whole point."""
    old = inpipe.get_by_name('reader')
    old.set_state(Gst.State.NULL)
    inpipe.remove(old)
    new = Gst.ElementFactory.make('mxlsrc', 'reader')
    new.set_property('domain', DOMAIN)
    new.set_property('video-flow-id', SRC_UUID)
    inpipe.add(new)
    if not new.link(q):
        print('swap: LINK FAILED — exiting for supervisor respawn', flush=True)
        import os
        os._exit(1)
    new.sync_state_with_parent()
    state['dry_swaps'] = state.get('dry_swaps', 0) + 1
    if state['dry_swaps'] > 20:
        # ~5min of fruitless swaps: something deeper than a reader wedge
        # (errored queue task, plugin state). One respawn recreates the
        # stable flow and costs one cascade — better than dead forever.
        print('20 dry swaps — last-resort exit for supervisor respawn', flush=True)
        import os
        os._exit(1)
    print('reader swapped (sink untouched)', flush=True)


def flow_inode():
    import os
    try:
        return os.stat(f'{DOMAIN}/{SRC_UUID}.mxl-flow').st_ino
    except OSError:
        return None


def watch():
    # Swap ONLY when the volatile flow is actually RECREATED (directory
    # inode changes) or newly appears; NOT on silence. An absent guest
    # keeps one validly attached reader that resumes when data returns —
    # silence-cycling (v1) churned ~4 swaps/min for nothing and muddied
    # the leg debugging.
    time.sleep(10)
    ino = flow_inode()
    while True:
        time.sleep(2)
        cur = flow_inode()
        if cur is not None and ino is not None and cur != ino:
            print('volatile flow RECREATED (inode changed) — swapping reader', flush=True)
            GLib.idle_add(swap_reader)
        elif cur is not None and ino is None:
            print('volatile flow appeared — swapping reader to attach', flush=True)
            GLib.idle_add(swap_reader)
        ino = cur


def boot_frame():
    # Push ONE near-black v210 frame at boot so the stable flow EXISTS from
    # the start — downstream (selector attach, layout branches) must never
    # find the flow missing (guest2's pane blocked on a not-yet-created
    # flow, 9/12). Word pattern packs 10-bit black-ish YCbCr.
    caps = Gst.Caps.from_string(
        'video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
        'interlace-mode=progressive,pixel-aspect-ratio=1/1,colorimetry=bt709')
    asrc.set_property('caps', caps)
    state['caps_set'] = True
    import struct
    row_words = ((1920 + 47) // 48) * 32          # v210: 48 px -> 32 words/group
    word = struct.pack('<I', (0x200 << 20) | (0x040 << 10) | 0x200)
    data = word * row_words * 1080
    buf = Gst.Buffer.new_wrapped(data)
    clock = outpipe.get_clock()
    now = (clock.get_time() - outpipe.get_base_time()) if clock else 0
    buf.pts = now + MARGIN_NS
    buf.dts = buf.pts
    buf.duration = FRAME_NS
    asrc.emit('push-buffer', buf)
    print('boot frame pushed — stable flow exists from t0', flush=True)


threading.Thread(target=watch, daemon=True).start()
threading.Thread(target=pull_loop, daemon=True).start()
threading.Thread(target=push_loop, daemon=True).start()

outpipe.set_state(Gst.State.PLAYING)
GLib.timeout_add(1500, lambda: (boot_frame(), False)[1])
inpipe.set_state(Gst.State.PLAYING)
print(f'flow_stabilizer {NAME} (freewheel): {SRC_UUID[:8]} -> {DST_UUID[:8]} running', flush=True)
loop = GLib.MainLoop()
bus = inpipe.get_bus()


def on_err(b, m):
    # THE PROCESS MUST BE IMMORTAL: a supervisor respawn would recreate the
    # stable flow and wedge every downstream reader — the exact failure this
    # tool exists to prevent. Stream errors surface from downstream elements
    # (queue) as often as from the reader itself, and they all mean the same
    # thing here: the volatile side broke. Swap the reader; never die.
    err = m.parse_error()
    src = m.src.get_name() if m.src else '?'
    print(f'gst error from {src}: {err[0].message} — swapping reader', flush=True)
    GLib.timeout_add(2000, lambda: (swap_reader(), False)[1])


bus.add_signal_watch()
bus.connect('message::error', on_err)
bus.connect('message::eos', lambda b, m:
            (print('EOS from volatile side — swapping reader', flush=True),
             GLib.timeout_add(2000, lambda: (swap_reader(), False)[1])))
loop.run()
raise SystemExit(1)
