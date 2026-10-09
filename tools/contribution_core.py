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

The restamp probe maps a remote source onto our local grain grid — do not "improve"
it without re-reading FINDINGS §1/§6 and §"Separate the output clock from the input":
  * MARGIN_NS = 2 grains. Bigger margins starve READERS (166ms => "too late" wedges).
  * The output is driven by a LOCAL WALL-CLOCK GRID, not by the source PTS (HW Oct-8):
    each grain is placed at `max(last_mapped + grain_ns, now + MARGIN_NS)` and any frame
    that would land more than one grain ahead of wall-clock is DROPPED. This is monotonic
    by construction (mxlsink has NO backward-index guard) and paced to real time. It
    replaced an offset-following STEP re-sync and then a proportional SLEW servo — both
    churned/ran away on a 60fps source decimated to 30 (the 1000ms SRT jitter buffer
    flushes a burst ~1s ahead of the just-started wall clock, so ANY offset-follows-source
    design breaks). HW-proven: clean 30fps, err bounded ~5-18ms, 0 restarts.
Grain timestamps are RING ADDRESSES, not metadata: a remote source MUST be restamped
onto the local clock cadence or it is not cuttable against local flows.

See docs/CONTRIBUTION-SEAM.md for the full spec and the ZoomISO-Cloud roadmap.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# --- canonical cadence constants (FINDINGS §1/§6 — load-bearing, do not tune blindly) ---
MARGIN_NS = 66_000_000      # 2 grains @30fps. Bigger => reader starvation ("too late" wedges).
FRAME_NS = 33_333_333       # 1 grain @30fps — fallback grain step when caps carry no framerate.
# Gross-discontinuity threshold: if the flow is already more than this far AHEAD of wall-clock,
# the grid re-anchors (continues from last_mapped) instead of drop-guarding — distinguishes a
# reconnect/clock-step (seconds ahead → re-anchor, don't stall) from a jitter-buffer burst
# (sub-second ahead → drop to stay real-time). 2s is above the 1000ms SRT jitter burst and
# below a real leg-rebuild gap.
RELOCK_GAP_NS = 2_000_000_000
# (The restamp drives a local wall-clock grid + drop-ahead-of-realtime; it does NOT use a
#  proportional slew or a hard-relock escape hatch — those were earlier designs the grid
#  replaced on HW Oct-8. See _restamp. The SLEW_* constants that lived here are gone.)

# --- canonical output formats: the chain's one true grain spec, per essence ---
CANON_CAPS = ('video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
              'pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709')
# Audio canonical grain: F32LE 48k stereo, matching audio_pgm.py's mixer caps so a
# contributed audio flow drops straight into the program mix with no reconvert.
CANON_AUDIO_CAPS = ('audio/x-raw,format=F32LE,layout=interleaved,rate=48000,'
                    'channels=2,channel-mask=(bitmask)0x3')
# Data (ANC / timed metadata) canonical grain: SMPTE ST 2038-wrapped ancillary, frame-
# aligned. mxlsink turns `meta/x-st-2038,alignment=frame` into a `video/smpte291` DATA flow
# (gst-mxl-rs v1.1.0). This is the path for CEA-608/708 captions, SCTE-104, etc. — the
# generalization of what v1.2 "Timed Data" (event flows, dmf-mxl #327) will make first-class.
CANON_DATA_CAPS = 'meta/x-st-2038,alignment=frame'
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
        suffix = {'audio': 'Audio', 'data': 'Data'}.get(self.essence, 'Video')
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
        self.state = {'offset': None, 'n': 0, 't0': None}
        # IN-005 ingress registry: AMWA IN-005 ("External Signal Ingress for DMF") asks that
        # a workload record each external signal's provenance and the timing adjustments it
        # applied, so they are TRACEABLE. We already compute all of it in _restamp; this
        # persists it as a per-ingest JSON record (mirrors guest_slot_watcher's /tmp map
        # pattern). Empty MXL_INGRESS_DIR disables it (default on so the quickstart writes it;
        # a cloned core still writes only to /tmp, no external calls). One file per flow.
        self.ingress_dir = _os.environ.get('MXL_INGRESS_DIR', '/tmp/mxl-ingress')
        self._ingress_path = (os.path.join(self.ingress_dir, f'{(adapter.flow_id or "src")[:8]}.json')
                              if self.ingress_dir else None)
        if self._ingress_path:   # create the dir ONCE here, not on every record write
            try:
                os.makedirs(self.ingress_dir, exist_ok=True)
            except Exception as _e:
                print(f'ingress dir setup skipped: {_e}', flush=True)
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
        if a.essence == 'data':
            # Data (ANC) conform: the adapter's source_fragment IS the conform here — it
            # ends at `meta/x-st-2038,alignment=frame` (e.g. caption text -> CEA-608 ->
            # cctost2038anc). We only enforce the canonical caps + a queue before mxlsink,
            # which writes the video/smpte291 data flow. No restamp: ST-2038 grains are
            # frame-aligned by the ANC wrapper (adapter sets timing_policy='preserve').
            return (f'{a.source_fragment()}'
                    f'! {CANON_DATA_CAPS} ! queue max-size-buffers=32 ! {sink}')
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
        if a.essence == 'data':
            return f'{jbuf} ! {CANON_DATA_CAPS} ! queue max-size-buffers=32 ! {sink}'
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

    def _write_ingress_record(self, pad, *, event, err_ns=None, grain_ns=None):
        """Persist the IN-005 ingress record for this flow (atomic, best-effort).

        `event` is the lifecycle tag that triggered the write ('locked', 'relocked',
        'hard-relock', 'diag'). Captures signal PROVENANCE (source caps/essence/transport)
        and the TIMING ADJUSTMENTS applied (locked offset, running wall-clock error, grain
        step) so an operator can see — from one file — what the restamp is doing, which is
        exactly what was invisible during the Oct-7 60fps churn. Never raises into the probe."""
        if not self._ingress_path:
            return
        try:
            s = self.state
            caps = pad.get_current_caps()
            rec = {
                'flow_id': self.a.flow_id,
                'label': self.a.label,
                'essence': self.a.essence,
                'transport': os.environ.get('MXL_GUEST_TRANSPORT', 'n/a'),
                'timing_policy': self.a.timing_policy,
                'event': event,
                'frames': s.get('n', 0),
                # timing adjustments (ns and ms for human + machine readers)
                'offset_ns': s.get('offset'),
                'offset_ms': round(s['offset'] / 1e6, 1) if s.get('offset') is not None else None,
                'err_ms': round(err_ns / 1e6, 1) if err_ns is not None else None,
                'grain_ns': grain_ns,
                'dropped': s.get('dropped', 0),   # ahead-of-realtime frames dropped (burst guard) —
                                                  # the grid servo's real health signal (replaces the
                                                  # old relock count; the grid has no relock path).
                # provenance: the negotiated source caps on the restamp pad
                'source_caps': caps.to_string() if caps else None,
                'mono_ns': time.monotonic_ns(),   # NOT wall time — no Date dependency, just ordering
            }
            tmp = self._ingress_path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(rec, f)
            os.replace(tmp, self._ingress_path)   # atomic swap — a reader never sees a half-write
        except Exception as e:
            print(f'ingress-record write skipped: {e}', flush=True)

    def _grain_ns(self, pad):
        """The monotonic grain step, derived from the NEGOTIATED output framerate on the
        restamp pad — NOT the 30fps `FRAME_NS` constant.

        The probe sits on the sink pad, AFTER the conform stage's
        `videorate ! ...framerate=30/1`, so for a conform source this is one grain of
        CANON_CAPS (33.3ms). But the servo/clamp must not BAKE IN 30fps: a 60fps source
        whose videorate hasn't fully settled can momentarily deliver buffers ~16.6ms
        apart. With a hardcoded 30fps clamp those map AHEAD of wall-clock faster than the
        grain clock — `mapped` races `now`, `err` runs away (the Oct-6 `err=-41s/fps=173`
        on a 60fps camera). Reading the actual framerate makes the step correct for
        whatever cadence the pad negotiated (30, 50, 59.94, …). Falls back to FRAME_NS if
        caps carry no framerate (e.g. pre-negotiation)."""
        caps = pad.get_current_caps()
        if caps and caps.get_size() > 0:
            ok, num, den = caps.get_structure(0).get_fraction('framerate')
            if ok and num > 0:
                return round(den * 1_000_000_000 / num)
        return FRAME_NS

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
        grain_ns = self._grain_ns(pad)   # rate-derived step (see _grain_ns); 30fps => FRAME_NS
        s = self.state
        if s['offset'] is None:
            # LOCK ANCHOR — a once-per-lock trigger + IN-005 provenance only. The grid below
            # does NOT use s['offset'] for pacing (HW Oct-8: the grid is driven by last_mapped +
            # now, see below); offset is recorded purely as "where wall-clock sat at lock" in the
            # ingress record. On a persistent-flow RE-LOCK the grid's own monotonic floor
            # (max(last_mapped + grain_ns, now + MARGIN)) already guarantees the first new-leg
            # grain lands after the last committed one — no offset clamp needed.
            s['offset'] = now - buf.pts + MARGIN_NS
            relock = bool(s.get('relock') and s.get('last_mapped') is not None)
            s['relock'] = False
            print(f'cadence {"RE-locked" if relock else "locked"} '
                  f'(grid-driven; anchor {s["offset"]/1e6:.0f}ms)', flush=True)
            self._write_ingress_record(pad, event=('relocked' if relock else 'locked'),
                                       grain_ns=grain_ns)
            if not relock and self.a.announce_on_lock and self.repair_url:
                threading.Thread(target=self._announce, daemon=True).start()
            elif not relock and self.a.announce_on_lock:
                print('announce skipped (no repair_url) — selector is pre-wired for this slot', flush=True)
        # LOCAL-GRID RESTAMP (HW Oct-8: the offset-follows-source approaches all failed on a
        # 60fps camera — the 1000ms SRT jitter buffer releases a BURST whose source PTS is ~1s
        # ahead of the just-started wall clock, so `buf.pts + offset` lands >1s off and every
        # earlier design — step re-sync, slew servo, monotonic+grain clamp — either churned or
        # hard-relock-looped to death). FINDINGS §"Separate the output clock from the input"
        # (v-final / flow_stabilizer) is explicit: drive output from a FIXED grain grid SLEWED
        # TO THE WALL CLOCK and DROP ahead-of-realtime arrivals. So we stop following buf.pts
        # entirely and drive a local grid:
        #   target = now + MARGIN_NS                     (where a live grain should sit)
        #   mapped = max(last_mapped + grain_ns, target) (one grain forward, never behind now)
        # This is monotonic by construction (mxlsink has NO backward-index guard — clock.rs —
        # so WE must guarantee it), paced to wall-clock (bursts can't race ahead: a frame that
        # would land before last_mapped+grain is simply placed on the next grid slot; a frame
        # far ahead is pulled back to `target`), and has no offset to drift or re-lock. grain_ns
        # is rate-derived so 50/59.94 grids work too. `err` is kept purely as a DIAGNOSTIC of
        # how far the raw source PTS sat from the grid (not a control input anymore)."""
        target = now + MARGIN_NS
        last_mapped = s.get('last_mapped')        # not in the initial state dict (HW Oct-8 KeyError)
        if last_mapped is None:
            mapped = target
        elif last_mapped - target > RELOCK_GAP_NS:
            # GROSS discontinuity, not a burst: the flow is already far (> RELOCK_GAP_NS) ahead
            # of wall-clock. This happens on a persistent-flow leg rebuild (10s of flow written,
            # the new leg's clock starts near 0) or a wall-clock step. If we treated this as a
            # burst we'd DROP every frame until `now` crawled up to last_mapped — a multi-second
            # stall on reconnect (regression caught by test_restamp_relock_never_rewinds). Instead
            # CONTINUE the grid from where the flow already is: one grain past last_mapped. The
            # flow stays monotonic (never rewinds past the committed head) and resumes immediately.
            mapped = last_mapped + grain_ns
        else:
            nxt = last_mapped + grain_ns
            # DROP ahead-of-realtime arrivals: next grid slot is up to one grain beyond where
            # wall-clock wants it — a burst/catch-up frame (e.g. the 1000ms SRT jitter buffer
            # flushing). Stamping it would march `mapped` ahead of `now` unbounded (the Oct-8
            # -30ms/frame runaway). Drop it; the grid stays locked to wall-clock. (Only reached
            # when the flow is NOT grossly ahead — i.e. a genuine burst, not a reconnect.)
            if nxt > target + grain_ns:
                s['dropped'] = s.get('dropped', 0) + 1
                return Gst.PadProbeReturn.DROP
            mapped = max(nxt, target)
        # GRID RESIDUAL (the real health signal, HW Oct-8): how far the emitted grain sits from
        # where wall-clock wants it. By construction this stays within ~one grain in steady
        # state; a value that grows means the grid is drifting ahead of realtime (the drop-guard
        # should prevent it). NOT the old source-PTS-vs-offset gap, which was meaningless on a
        # grid servo (it read -10s on HW while the flow was a perfectly healthy 30fps grid).
        err = mapped - target
        buf.pts = mapped
        buf.duration = grain_ns     # stamp an explicit one-grain duration (mirrors the v4 servo)
        s['last_mapped'] = mapped
        s['n'] += 1
        if s['t0'] is None:
            s['t0'] = now
        if s['n'] % self.diag_every == 0:
            el = (now - s['t0']) / 1e9
            # fps over the FULL window since t0 (not a per-tick burst) → the committed
            # cadence, ~grain rate in steady state. A persistently high value means
            # videorate isn't decimating (source-rate mismatch worth investigating).
            fps = s['n'] / el if el > 0 else 0
            print(f"diag n={s['n']} fps={fps:.2f} err={err/1e6:.0f}ms "
                  f"grain={grain_ns/1e6:.1f}ms dropped={s.get('dropped', 0)}", flush=True)
            self._write_ingress_record(pad, event='diag', err_ns=err, grain_ns=grain_ns)
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
        target = now + MARGIN_NS
        last = s.get('last_mapped')
        # Re-anchor when we have no anchor yet, OR we've drifted far from wall-clock. The OLD
        # code re-anchored to `now + MARGIN` UNCONDITIONALLY on a >500ms gap — but on bursty
        # fan-out delivery `next_pts` races AHEAD (it advances by buf.duration per buffer), so
        # the re-anchor JUMPED next_pts BACKWARD to now+MARGIN → non-monotonic PTS into mxlsink
        # (no backward-index guard) → "streaming stopped, reason error (-5)" → restart loop
        # (HW Oct-8, reproduced with a live camera's AAC through the A/V fan-out). FIX: the
        # anchor floors at last_mapped + one audio quantum, so a re-anchor can only ever nudge
        # FORWARD — never rewind the flow. (Mirrors the video grid's monotonic guarantee.)
        if s.get('next_pts') is None or abs(target - s['next_pts']) > 500_000_000:
            new_anchor = target
            if last is not None:
                # never place audio at/behind the last sample already written (one buffer's
                # worth of headroom; buf.duration is this buffer's span)
                quantum = buf.duration if buf.duration != Gst.CLOCK_TIME_NONE else FRAME_NS
                new_anchor = max(new_anchor, last + quantum)
            relock = bool(s.get('relock') and last is not None)
            s['relock'] = False
            s['next_pts'] = new_anchor
            if s['offset'] is None:   # first lock -> announce if asked (parity w/ video)
                s['offset'] = new_anchor
                print(f'audio cadence anchored: {new_anchor/1e6:.0f}ms', flush=True)
                if self.a.announce_on_lock and self.repair_url:
                    threading.Thread(target=self._announce, daemon=True).start()
                elif self.a.announce_on_lock:
                    print('announce skipped (no repair_url) — mixer tolerates absent flows', flush=True)
            else:
                print(f'audio cadence {"RE-ANCHORED" if relock else "re-anchored"} (monotonic): '
                      f'{new_anchor/1e6:.0f}ms', flush=True)
        buf.pts = s['next_pts']
        s['last_mapped'] = s['next_pts']   # for a monotonic re-anchor on the next rebuild
        if buf.duration != Gst.CLOCK_TIME_NONE:
            s['next_pts'] += buf.duration
        return Gst.PadProbeReturn.OK

    def run(self):
        policy = self.a.timing_policy
        # Data (ANC) grains are frame-aligned by the ST-2038 wrapper and carry their own
        # timing — there is no sensible video/audio restamp for them. Force 'preserve' so
        # the probe is never attached, regardless of what the adapter requested.
        if self.a.essence == 'data' and policy == 'restamp':
            print("essence=data: forcing timing_policy=preserve (ST-2038 grains are "
                  "frame-aligned; no restamp)", flush=True)
            policy = 'preserve'
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
