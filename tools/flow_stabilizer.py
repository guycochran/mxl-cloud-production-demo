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

# sink side runs FOREVER; the reader ahead of the queue is replaceable
pipe = Gst.parse_launch(
    f'mxlsrc name=reader domain={DOMAIN} video-flow-id={SRC_UUID} '
    f'! queue name=q max-size-buffers=8 leaky=downstream '
    f'! mxlsink name=sink domain={DOMAIN} flow-id={DST_UUID} '
    f'label="{LABEL}" description="stabilized {NAME} (wedge-proof indirection)" '
    f'group-hint="{NAME.capitalize()}Stable:Video" sync=false')

q = pipe.get_by_name('q')
sink = pipe.get_by_name('sink')

state = {'off': None, 'last_buf': 0.0, 'n': 0, 't0': None}


def restamp(pad, info):
    buf = info.get_buffer()
    state['last_buf'] = time.monotonic()
    state['dry_swaps'] = 0  # data flows — swaps (if any) worked
    clock = pipe.get_clock()
    if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
        return Gst.PadProbeReturn.OK
    now = clock.get_time() - pipe.get_base_time()
    # offset-lock (cam_relay pattern): follow the source cadence through one
    # offset, hard re-lock on >500ms error (fresh reader / recreated source)
    if state['off'] is None or abs((buf.pts + state['off']) - (now + MARGIN_NS)) > 500_000_000:
        state['off'] = now + MARGIN_NS - buf.pts
        print(f'offset locked ({state["off"] / 1e6:.0f}ms)', flush=True)
    buf.pts += state['off']
    state['n'] += 1
    if state['t0'] is None:
        state['t0'] = now
    if state['n'] % 300 == 0:
        el = (now - state['t0']) / 1e9
        print(f'diag n={state["n"]} fps={state["n"] / el:.2f}', flush=True)
    return Gst.PadProbeReturn.OK


sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, restamp)


def swap_reader():
    """Replace the mxlsrc in-place. The sink never stops -> the stable flow
    is never recreated -> downstream readers never wedge. This is the whole
    point of the tool."""
    old = pipe.get_by_name('reader')
    old.set_state(Gst.State.NULL)
    pipe.remove(old)
    new = Gst.ElementFactory.make('mxlsrc', 'reader')
    new.set_property('domain', DOMAIN)
    new.set_property('video-flow-id', SRC_UUID)
    pipe.add(new)
    if not new.link(q):
        print('swap: LINK FAILED — exiting for supervisor respawn', flush=True)
        import os
        os._exit(1)
    new.sync_state_with_parent()
    state['off'] = None  # fresh reader timeline -> re-lock on first buffer
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


threading.Thread(target=watch, daemon=True).start()

pipe.set_state(Gst.State.PLAYING)
print(f'flow_stabilizer {NAME}: {SRC_UUID[:8]} -> {DST_UUID[:8]} running', flush=True)
loop = GLib.MainLoop()
bus = pipe.get_bus()


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
