#!/usr/bin/env python3
"""ContributionCore — the proven back half of every MXL contribution ingest.

Every contribution path in this repo (cam, cam2/Makito, guest SRT, and — when the
beta lands — ZoomISO Cloud) is the same five stages:

    [TRANSPORT FRONT END] -> [CONFORM to v210] -> [RESTAMP +2gr] -> [SINK to slot] -> [ANNOUNCE]
         varies                  identical          identical        identical         identical

Only the FRONT END varies. This module owns the four identical, hard-won stages so
there is ONE place that carries the FINDINGS lessons instead of three copy-pasted
scripts. A transport is expressed as a `SourceAdapter`; `ContributionCore.run()`
wires it to the canonical conform + restamp + mxlsink + announce and runs the loop.

The restamp probe here is byte-for-byte the one that shipped in cam_ingest.py and
guest_ingest.py — do not "improve" it without re-reading FINDINGS §1/§6:
  * MARGIN_NS = 2 grains. Bigger margins starve READERS (166ms => "too late" wedges).
  * RESYNC only after RESYNC_COUNT consecutive out-of-band frames, so a momentary
    network hiccup never yanks the offset.
Grain timestamps are RING ADDRESSES, not metadata: a remote source MUST be restamped
onto the local clock cadence or it is not cuttable against local flows.

See docs/CONTRIBUTION-SEAM.md for the full spec and the ZoomISO-Cloud roadmap.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# --- canonical cadence constants (FINDINGS §1/§6 — load-bearing, do not tune blindly) ---
MARGIN_NS = 66_000_000      # 2 grains @30fps. Bigger => reader starvation ("too late" wedges).
FRAME_NS = 33_333_333       # 1 grain @30fps — the monotonic step used on a persistent re-lock.
RESYNC_NS = 150_000_000     # out-of-band threshold before we consider re-locking the offset
RESYNC_COUNT = 45           # consecutive out-of-band frames required to actually re-sync

# --- canonical output formats: the chain's one true grain spec, per essence ---
CANON_CAPS = ('video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
              'pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709')
# Audio canonical grain: F32LE 48k stereo, matching audio_pgm.py's mixer caps so a
# contributed audio flow drops straight into the program mix with no reconvert.
CANON_AUDIO_CAPS = ('audio/x-raw,format=F32LE,layout=interleaved,rate=48000,'
                    'channels=2,channel-mask=(bitmask)0x3')
DEFAULT_DOMAIN = '/mxl-domain'


def _ctl_headers(h):
    """Add X-MXL-Token when MXL_CONTROL_TOKEN is set (backend control-route auth, SECURITY.md).
    No env var => headers unchanged."""
    tok = os.environ.get('MXL_CONTROL_TOKEN', '').strip()
    if tok:
        h = dict(h, **{'X-MXL-Token': tok})
    return h


class SourceAdapter(ABC):
    """A pluggable contribution transport. Implementations produce the LAUNCH
    FRAGMENT for their front end (everything up to, but not including, the
    canonical conform/v210 caps + mxlsink), and declare their routing metadata.

    A conform adapter's fragment must end at a raw-ish video pad that the core
    can feed into `! videorate ! videoscale ! videoconvert ! <CANON_CAPS> ! mxlsink`.
    A native-MXL adapter already emits v210 grains and sets needs_conform=False, so
    the core will NOT re-conform — see _build_launch().

    *** Orthogonal properties (do NOT collapse these into one "is native" flag). ***
    "Transport is MXL" and "already aligned to my production domain clock" are
    DIFFERENT questions — a native-MXL source can still arrive on a foreign clock and
    need restamping. The core makes exactly two decisions, driven by two properties:
      - needs_conform  -> whether to run videorate/videoscale/videoconvert/v210
      - timing_policy  -> what to do with grain timestamps (see below)
    is_native_mxl remains as a convenience that sets both to the common native case,
    but a native source is free to override timing_policy once measured."""

    # --- routing / identity ---
    label: str = 'Source'               #: human label + switcher-button/group routing
    flow_id: str = ''                   #: target MXL flow UUID == selector slot
    description: str = 'contribution ingest'

    # --- front-end physics ---
    latency_ms: int = 200               #: SRT/RTSP jitterbuffer hint (ms); per-path physics

    #: keep the mxlsink FLOW alive across a source reconnect instead of exiting the
    #: process (which recreates the flow and wedges the selector's cold reader — review
    #: R1). When True, ContributionCore.run() rebuilds ONLY the source leg on EOS/error
    #: and relinks it into the persistent sink; the flow UUID is created once and never
    #: recreated, and the restamp offset stays locked so the local-clock cadence is
    #: continuous (no "grain too early"). Opt-in, env MXL_INGEST_PERSISTENT=1. Default
    #: False preserves the HW-proven supervisor-restart behaviour until verified.
    persistent_flow: bool = False

    #: which media essence this adapter contributes. Selects the conform caps/
    #: elements and the restamp probe. 'video' (default) or 'audio'. A participant
    #: (guest/cam) with both runs ONE adapter+core per essence (never multiplexed).
    essence: str = 'video'

    # --- the two orthogonal pipeline decisions the core actually makes ---
    #: run the v210 conform stage? False only when the source already emits canonical grains.
    needs_conform: bool = True
    #: 'restamp' = map onto local clock +2gr (remote/foreign-clock sources; the default);
    #: 'align'   = source claims domain alignment, MEASURE before trusting (ZoomISO TBD);
    #: 'preserve'= pass timestamps untouched (only if proven already on our domain clock).
    timing_policy: str = 'restamp'

    #: fire the /api/mxl/repair announce on first locked frame (guests/cams).
    announce_on_lock: bool = True

    @property
    def is_native_mxl(self) -> bool:
        """Back-compat convenience: a source that needs no conform. Does NOT imply a
        timing_policy — ask timing_policy for that."""
        return not self.needs_conform

    @property
    def group_hint(self) -> str:
        suffix = 'Audio' if self.essence == 'audio' else 'Video'
        return f'{self.label.replace(" ", "")}:{suffix}'

    @abstractmethod
    def source_fragment(self) -> str:
        """gst-launch fragment for the FRONT END, ending with a trailing '! '.
        For a conform adapter this is `src ... ! avdec_* ! queue`. For a native-MXL
        adapter this is the `mxlsrc ...` that produces v210 grains directly."""
        ...


class ContributionCore:
    """Wires a SourceAdapter to the canonical conform + restamp + mxlsink + announce,
    then runs the GLib main loop. One instance per ingest process (matches the
    one-process-per-source supervisor model the demo already uses)."""

    def __init__(self, adapter: SourceAdapter, domain: str = DEFAULT_DOMAIN,
                 repair_url: str = None, diag_every: int = 300):
        import os as _os
        self.a = adapter
        self.domain = _os.environ.get('MXL_DOMAIN', domain)
        # Announce target resolution, in priority order:
        #   explicit repair_url arg  >  MXL_REPAIR_URL env  >  NO announce (default).
        # SECURITY / BOUNDARY: the open-source core must make NO external assumptions.
        # An adopter who clones this repo and runs an ingest must never unknowingly
        # call someone else's backend — so with nothing configured there is no announce
        # target at all. A deployment opts IN explicitly:
        #     MXL_REPAIR_URL=https://prodbots.com/api/mxl/repair python3 cam_ingest.py
        # (the OHG live bring-up sets exactly that; see scripts/bring-up-mxl.sh). The
        # quickstart tier pre-wires guest slots into the selector, so it needs no
        # announce — it leaves MXL_REPAIR_URL unset (or ="none") and stays self-contained.
        if repair_url is None:
            repair_url = _os.environ.get('MXL_REPAIR_URL', '')
        self.repair_url = None if repair_url.strip().lower() in ('', 'none') else repair_url
        self.diag_every = diag_every
        self.state = {'offset': None, 'drift_n': 0, 'n': 0, 't0': None}
        # Persistent-flow mode: adapter property OR env override (env wins when set),
        # so an adopter can turn it on for an existing adapter without editing it.
        env_persist = _os.environ.get('MXL_INGEST_PERSISTENT', '').strip().lower()
        if env_persist in ('1', 'true', 'yes'):
            self.persistent = True
        elif env_persist in ('0', 'false', 'no'):
            self.persistent = False
        else:
            self.persistent = bool(getattr(adapter, 'persistent_flow', False))
        self._reconnects = 0
        Gst.init(None)
        self.loop = GLib.MainLoop()
        if self.persistent:
            # Build the persistent TAIL (queue -> [conform] -> mxlsink) once; the source
            # leg is added + linked separately so it can be rebuilt in place on reconnect.
            self.pipe = Gst.Pipeline.new('ingest')
            self._build_persistent_tail()
            self._build_source_leg()   # first attach
        else:
            self.pipe = Gst.parse_launch(self._build_launch())
            self.sink = self.pipe.get_by_name('sink')

    def _build_launch(self) -> str:
        a = self.a
        sink = (f'mxlsink name=sink domain={self.domain} flow-id={a.flow_id} '
                f'label="{a.label}" description="{a.description}" '
                f'group-hint="{a.group_hint}" sync=false')
        if not a.needs_conform:
            # Grains already canonical — do NOT re-conform. (native-MXL path.)
            return f'{a.source_fragment()}! {sink}'
        if a.essence == 'audio':
            # Audio conform: front end -> F32LE/48k/2ch -> queue -> mxlsink. Byte-for-byte
            # the chain guest_audio.py shipped (the queue sits AFTER the caps here, unlike
            # the video stage). audioresample reconciles any input rate to 48k; audioconvert
            # any layout/format to F32LE interleaved.
            return (f'{a.source_fragment()}'
                    f'! audioconvert ! audioresample ! {CANON_AUDIO_CAPS} '
                    f'! queue max-size-buffers=32 ! {sink}')
        # Video decode path: front end -> canonical conform -> mxlsink.
        # videorate reconciles any 30000/1001 (etc.) to exact 30/1 — WITHOUT it the
        # v210 capsfilter intermittently fails to negotiate (documented crash). It
        # lives here (conform), applied once; the adapter front end ends at the queue.
        return (f'{a.source_fragment()}'
                f'! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 '
                f'! {CANON_CAPS} ! {sink}')

    # ---------------------------------------------------------------------------
    # Persistent-flow mode (review R1): the mxlsink flow is created ONCE and kept
    # alive across source reconnects. The pipeline is split at a named `jbuf` queue:
    #   [ source leg ]  →  jbuf  →  [ conform ]  →  mxlsink   (tail = persistent)
    # On source EOS/error we set only the source-leg elements to NULL, drop them,
    # rebuild them from the SAME adapter fragment, and relink into jbuf. The sink,
    # the flow, the clock and the restamp offset all survive — so the selector's
    # reader never sees a flow recreation and can never wedge, and the local-clock
    # cadence is continuous (no "grain too early"). Same restamp probe as the
    # one-shot path, so lip-sync timing is byte-identical.
    # ---------------------------------------------------------------------------
    def _tail_fragment(self) -> str:
        """The persistent tail: a jitter queue the source leg links into, then the
        essence-appropriate conform, then the mxlsink. Identical conform/sink to
        _build_launch so timing + grain spec don't diverge between modes."""
        a = self.a
        sink = (f'mxlsink name=sink domain={self.domain} flow-id={a.flow_id} '
                f'label="{a.label}" description="{a.description}" '
                f'group-hint="{a.group_hint}" sync=false')
        # leaky=downstream: during the reconnect gap no buffers arrive; when the new
        # leg floods to catch up we drop the oldest rather than block the sink thread.
        jbuf = 'queue name=jbuf leaky=downstream max-size-buffers=8'
        if not a.needs_conform:
            return f'{jbuf} ! {sink}'
        if a.essence == 'audio':
            return (f'{jbuf} ! audioconvert ! audioresample ! {CANON_AUDIO_CAPS} '
                    f'! queue max-size-buffers=32 ! {sink}')
        return (f'{jbuf} ! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 '
                f'! {CANON_CAPS} ! {sink}')

    def _build_persistent_tail(self):
        """Parse the tail fragment into a bin, add it to the pipeline, cache handles."""
        tail = Gst.parse_bin_from_description(self._tail_fragment(), False)
        self.pipe.add(tail)
        self._tail = tail
        self.sink = tail.get_by_name('sink')
        self.jbuf = tail.get_by_name('jbuf')

    def _build_source_leg(self):
        """(Re)build the source leg from the adapter fragment and link it into jbuf.
        The leg is everything the one-shot path puts BEFORE the conform — i.e. the
        adapter's own fragment, which already ends at a raw pad (conform adapters) or
        at v210 grains (native adapters). We link the leg's ghosted src pad to jbuf's
        STATIC sink pad explicitly, so a rebuild can't accidentally grab a different
        pad or fail because the element-level link picks the wrong one."""
        frag = self.a.source_fragment().strip()
        if frag.endswith('!'):
            frag = frag[:-1].strip()
        leg = Gst.parse_bin_from_description(frag, True)  # ghost the trailing src pad
        leg.set_name('srcleg')
        self.pipe.add(leg)
        self._leg = leg
        # Element-level link: it resolves the leg's ghost src pad to jbuf's sink pad
        # internally. (A manual ghost-pad-to-ghost-pad pad.link() returns wrong-hierarchy
        # for bins at the same level; Element.link does the right thing.) The jbuf sink
        # must be free — the rebuild path unlinks the old leg before calling us.
        jbuf_sink = self.jbuf.get_static_pad('sink')
        if jbuf_sink is not None and jbuf_sink.is_linked():
            raise RuntimeError('persistent-flow: jbuf sink still linked (old leg not released)')
        if not leg.link(self.jbuf):
            raise RuntimeError('persistent-flow: could not link source leg -> jbuf')
        leg.sync_state_with_parent()

    def _rebuild_source_leg(self):
        """On source EOS/error: tear down ONLY the source leg, rebuild + relink it.
        Runs on the GLib main thread (scheduled via idle_add from the bus callback)."""
        self._reconnects += 1
        print(f'persistent-flow: source dropped — rebuilding leg (reconnect #{self._reconnects}), '
              f'flow {self.a.flow_id[:8]} stays live', flush=True)
        # Re-anchor the restamp: the new source leg restarts its PTS near 0 (a new
        # RTP/SRT/encoder session on reconnect — same for a real camera), so the OLD
        # offset would map the new frames BACKWARD and the flow's grain index would jump
        # back → a selector reading the flow sees "grain … too early". Clear the lock so
        # the probe re-locks on the new leg's first frame, and remember the last grain we
        # wrote so the re-lock can be clamped to keep the flow MONOTONIC (never rewind).
        self.state['offset'] = None
        self.state['next_pts'] = None   # audio anchor, re-anchored the same way
        self.state['relock'] = True
        try:
            old = getattr(self, '_leg', None)
            if old is not None:
                # Free jbuf's sink pad before relinking. Unlink by jbuf's OWN sink-pad
                # peer (not the leg's ghost pad, which after EOS may report linked but
                # resolve to nothing through element-level unlink). set NULL + remove the
                # leg too so its elements and ghost target are fully torn down.
                jsink = self.jbuf.get_static_pad('sink')
                peer = jsink.get_peer() if jsink is not None else None
                if peer is not None:
                    peer.unlink(jsink)
                old.set_state(Gst.State.NULL)
                self.pipe.remove(old)
                self._leg = None
                if jsink is not None and jsink.is_linked():
                    raise RuntimeError('persistent-flow: jbuf sink still linked after unlink+remove')
            self._build_source_leg()
        except Exception as e:
            # Can't recover the leg in-place — fall back to a full process restart so the
            # supervisor loop takes over (worst case = old behaviour, never silent death).
            print(f'persistent-flow: leg rebuild failed ({e}); exiting for supervisor restart', flush=True)
            self.loop.quit()
            return False
        return False  # one-shot idle

    def _from_tail(self, msg_src) -> bool:
        """True if a bus message originated inside the persistent tail bin (sink/conform)
        rather than the source leg. Tail errors are fatal (can't relink away a broken
        sink); source-leg errors are recoverable by rebuilding the leg."""
        tail = getattr(self, '_tail', None)
        if msg_src is None or tail is None:
            return True  # unknown origin — treat as fatal (safe default)
        node = msg_src
        while node is not None:
            if node is tail:
                return True
            node = node.get_parent()
        return False

    # --- the announce loop (verbatim from guest_ingest, incl. the 429-escalation) ---
    def _announce(self):
        """Tell the backend a flow just appeared so the selector re-attaches.
        The backend rate-limits auto cascades (429 on cooldown); retry patiently,
        with a once-per-5min full-strength escalation if auto announces starve."""
        import os as _os
        import time as _t
        mark_key = self.a.flow_id[:8] or 'src'
        attempt = 0
        while True:
            attempt += 1
            try:
                r = urllib.request.Request(self.repair_url, data=json.dumps({'auto': 1}).encode(),
                                           headers=_ctl_headers({'Content-Type': 'application/json',
                                                                     'User-Agent': 'contribution-core/1.0'}))
                with urllib.request.urlopen(r, timeout=60) as resp:
                    print(f'announce -> {resp.status} {resp.read()[:120]}', flush=True)
                    return
            except urllib.error.HTTPError as e:
                print(f'announce {e.code} (attempt {attempt+1})', flush=True)
                if e.code not in (409, 429):
                    return
                if e.code == 429 and attempt >= 3:
                    mark = f'/tmp/{mark_key}-escalate.ts'
                    last = _os.path.getmtime(mark) if _os.path.exists(mark) else 0
                    if _t.time() - last > 300:
                        open(mark, 'w').close()
                        try:
                            r2 = urllib.request.Request(self.repair_url, data=b'{}',
                                                        headers=_ctl_headers({'Content-Type': 'application/json',
                                                                              'User-Agent': 'contribution-core/1.0'}))
                            with urllib.request.urlopen(r2, timeout=60) as resp:
                                print(f'announce ESCALATED -> {resp.status} {resp.read()[:120]}', flush=True)
                                return
                        except Exception as e2:
                            print(f'escalation failed: {e2} — back to patient retries', flush=True)
            except Exception as e:
                print(f'announce err: {e} (attempt {attempt+1})', flush=True)
            _t.sleep(30)

    # --- the cadence-preserving restamp (verbatim; the single most load-bearing idea) ---
    def _restamp(self, pad, info):
        # Wait for caps before touching buffers — a dynamic-pad demux (tsdemux on the
        # SRT path) negotiates the sink pad's caps after PLAYING; restamping first
        # breaks negotiation. Harmless for static-pad sources (caps present at once).
        if pad.get_current_caps() is None:
            return Gst.PadProbeReturn.OK
        buf = info.get_buffer()
        clock = self.pipe.get_clock()
        if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        now = clock.get_time() - self.pipe.get_base_time()
        s = self.state
        if s['offset'] is None:
            s['offset'] = now - buf.pts + MARGIN_NS
            # On a RE-LOCK (persistent-flow leg rebuild) the flow already has grains up to
            # last_mapped; clamp the offset so the first new-leg grain lands STRICTLY after
            # that (never rewind the flow — a rewind is exactly the "grain too early" read).
            if s.get('relock') and s.get('last_mapped') is not None:
                min_pts = s['last_mapped'] + FRAME_NS
                if buf.pts + s['offset'] < min_pts:
                    s['offset'] = min_pts - buf.pts
                print(f'cadence offset RE-LOCKED (monotonic): {s["offset"]/1e6:.0f}ms', flush=True)
                s['relock'] = False
            else:
                print(f'cadence offset locked: {s["offset"]/1e6:.0f}ms', flush=True)
                if self.a.announce_on_lock and self.repair_url:
                    threading.Thread(target=self._announce, daemon=True).start()
                elif self.a.announce_on_lock:
                    print('announce skipped (no repair_url) — selector is pre-wired for this slot', flush=True)
        mapped = buf.pts + s['offset']
        err = now + MARGIN_NS - mapped
        if abs(err) > RESYNC_NS:
            s['drift_n'] += 1
            if s['drift_n'] >= RESYNC_COUNT:
                s['offset'] += err
                s['drift_n'] = 0
                print(f'cadence re-synced by {err/1e6:.0f}ms', flush=True)
                mapped = buf.pts + s['offset']
        else:
            s['drift_n'] = 0
        buf.pts = mapped
        s['last_mapped'] = mapped   # for a monotonic re-lock on the next leg rebuild
        s['n'] += 1
        if s['t0'] is None:
            s['t0'] = now
        if s['n'] % self.diag_every == 0:
            el = (now - s['t0']) / 1e9
            fps = s['n'] / el if el > 0 else 0
            print(f"diag n={s['n']} fps={fps:.2f} err={err/1e6:.0f}ms", flush=True)
        return Gst.PadProbeReturn.OK

    # --- the AUDIO restamp (verbatim from guest_audio.py) ---
    # Audio can't use the video offset-map: audio buffers carry a sample-count
    # DURATION, so we lay them end-to-end on a monotonically advancing PTS anchored
    # to now+MARGIN, re-anchoring only on a big (>500ms) gap. Same clock + MARGIN as
    # the video probe, so a participant's two essences share one time base (see
    # docs/AV-CONTRIBUTION-v0.3.md §5 open-question 1 on lip-sync).
    def _restamp_audio(self, pad, info):
        # With a dynamic-pad demux (tsdemux on the SRT path) the sink pad is linked
        # and caps-negotiated AFTER PLAYING; touching buffers before caps settle
        # breaks negotiation (not-negotiated -4). Wait for caps before restamping.
        if pad.get_current_caps() is None:
            return Gst.PadProbeReturn.OK
        buf = info.get_buffer()
        clock = self.pipe.get_clock()
        if not clock:
            return Gst.PadProbeReturn.OK
        now = clock.get_time() - self.pipe.get_base_time()
        s = self.state
        if s.get('next_pts') is None or abs(now + MARGIN_NS - s['next_pts']) > 500_000_000:
            s['next_pts'] = now + MARGIN_NS
            # Monotonic re-anchor on a persistent-flow leg rebuild: never place the new
            # leg's audio before the last sample we already wrote (same rule as video).
            if s.get('relock') and s.get('last_mapped') is not None:
                s['next_pts'] = max(s['next_pts'], s['last_mapped'] + FRAME_NS)
                print(f'audio cadence RE-ANCHORED (monotonic): {s["next_pts"]/1e6:.0f}ms', flush=True)
                s['relock'] = False
            if s['offset'] is None:   # first lock -> announce if asked (parity w/ video)
                s['offset'] = s['next_pts']
                print(f'audio cadence anchored: {s["next_pts"]/1e6:.0f}ms', flush=True)
                if self.a.announce_on_lock and self.repair_url:
                    threading.Thread(target=self._announce, daemon=True).start()
                elif self.a.announce_on_lock:
                    print('announce skipped (no repair_url) — mixer tolerates absent flows', flush=True)
        buf.pts = s['next_pts']
        s['last_mapped'] = s['next_pts']   # for a monotonic re-anchor on the next rebuild
        if buf.duration != Gst.CLOCK_TIME_NONE:
            s['next_pts'] += buf.duration
        return Gst.PadProbeReturn.OK

    def run(self):
        policy = self.a.timing_policy
        # pick the essence-appropriate restamp probe (video offset-map vs audio
        # duration-accumulate). Both reference the same clock + MARGIN_NS.
        restamp_probe = self._restamp_audio if self.a.essence == 'audio' else self._restamp
        if policy == 'restamp':
            # Default: map the source onto the local clock (+2 grains). The probe
            # fires the announce itself on offset-lock.
            self.sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, restamp_probe)
        else:
            # No restamp (align|preserve): grains carry their own timing. The probe
            # won't run, so announce directly here if requested.
            if policy == 'align':
                print('timing_policy=align: trusting source domain alignment '
                      '(ASSUMED, not verified — measure before relying on it)', flush=True)
            elif policy == 'preserve':
                print('timing_policy=preserve: passing timestamps untouched', flush=True)
            else:
                print(f'WARN unknown timing_policy "{policy}"; defaulting to restamp', flush=True)
                self.sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, restamp_probe)
            if self.a.announce_on_lock and self.repair_url and policy in ('align', 'preserve'):
                threading.Thread(target=self._announce, daemon=True).start()
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        if self.persistent:
            # Keep the flow alive: on a SOURCE-LEG eos/error, rebuild just that leg.
            # (A fatal error from the sink/tail still exits for a supervisor restart —
            # that can't be recovered by relinking.)
            def _on_err(b, m):
                err, _dbg = m.parse_error()
                src = m.src.get_name() if m.src else '?'
                sys.stderr.write(f'ERR [{src}] {err}\n')
                if self._from_tail(m.src):
                    self.loop.quit()            # fatal in the persistent tail — restart
                else:
                    GLib.idle_add(self._rebuild_source_leg)
            def _on_eos(b, m):
                sys.stderr.write('EOS (source) — persistent flow, rebuilding leg\n')
                GLib.idle_add(self._rebuild_source_leg)
            bus.connect('message::error', _on_err)
            bus.connect('message::eos', _on_eos)
        else:
            bus.connect('message::error',
                        lambda b, m: (sys.stderr.write(f'ERR {m.parse_error()}\n'), self.loop.quit()))
            bus.connect('message::eos',
                        lambda b, m: (sys.stderr.write('EOS\n'), self.loop.quit()))
        self.pipe.set_state(Gst.State.PLAYING)
        print(f'contribution_core running: {self.a.label} -> {self.a.flow_id}', flush=True)
        try:
            self.loop.run()
        finally:
            # Release the pipeline — crucially the mxlsink WRITER, which lives in the
            # shared-memory domain, not the process. set_state(NULL) is async, so WAIT
            # for it to finish before exiting: if we exit while the writer is still
            # held, the supervisor's 2s-later restart hits mxlsink "the UUID belongs to
            # a flow with another active writer" and the source never re-attaches
            # (observed Oct 5, surfaced as a misleading srtsrc not-negotiated loop).
            self.pipe.set_state(Gst.State.NULL)
            self.pipe.get_state(Gst.CLOCK_TIME_NONE)  # block until NULL is reached → writer released
            sys.exit(1)  # abnormal by definition; supervisor restarts (idle 404 loop is cheap)
