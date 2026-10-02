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
RESYNC_NS = 150_000_000     # out-of-band threshold before we consider re-locking the offset
RESYNC_COUNT = 45           # consecutive out-of-band frames required to actually re-sync

# --- canonical output format: the chain's one true grain spec ---
CANON_CAPS = ('video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,'
              'pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709')
DEFAULT_DOMAIN = '/mxl-domain'


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
        return f'{self.label.replace(" ", "")}:Video'

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
        #   explicit repair_url arg  >  MXL_REPAIR_URL env  >  prodbots default.
        # Set MXL_REPAIR_URL="" (or "none") to DISABLE the backend announce — the
        # quickstart tier does this: it pre-wires guest slots into the selector, so
        # no backend re-attach is needed (fully self-contained, no prodbots).
        if repair_url is None:
            repair_url = _os.environ.get('MXL_REPAIR_URL', 'https://prodbots.com/api/mxl/repair')
        self.repair_url = None if repair_url.strip().lower() in ('', 'none') else repair_url
        self.diag_every = diag_every
        self.state = {'offset': None, 'drift_n': 0, 'n': 0, 't0': None}
        Gst.init(None)
        self.pipe = Gst.parse_launch(self._build_launch())
        self.sink = self.pipe.get_by_name('sink')
        self.loop = GLib.MainLoop()

    def _build_launch(self) -> str:
        a = self.a
        sink = (f'mxlsink name=sink domain={self.domain} flow-id={a.flow_id} '
                f'label="{a.label}" description="{a.description}" '
                f'group-hint="{a.group_hint}" sync=false')
        if not a.needs_conform:
            # Grains already canonical — do NOT re-conform. (native-MXL path.)
            return f'{a.source_fragment()}! {sink}'
        # Decode path: front end -> canonical conform -> mxlsink.
        # videorate reconciles any 30000/1001 (etc.) to exact 30/1 — WITHOUT it the
        # v210 capsfilter intermittently fails to negotiate (documented crash). It
        # lives here (conform), applied once; the adapter front end ends at the queue.
        return (f'{a.source_fragment()}'
                f'! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 '
                f'! {CANON_CAPS} ! {sink}')

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
                                           headers={'Content-Type': 'application/json',
                                                    'User-Agent': 'contribution-core/1.0'})
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
                                                        headers={'Content-Type': 'application/json',
                                                                 'User-Agent': 'contribution-core/1.0'})
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
        buf = info.get_buffer()
        clock = self.pipe.get_clock()
        if not clock or buf.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        now = clock.get_time() - self.pipe.get_base_time()
        s = self.state
        if s['offset'] is None:
            s['offset'] = now - buf.pts + MARGIN_NS
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
        s['n'] += 1
        if s['t0'] is None:
            s['t0'] = now
        if s['n'] % self.diag_every == 0:
            el = (now - s['t0']) / 1e9
            fps = s['n'] / el if el > 0 else 0
            print(f"diag n={s['n']} fps={fps:.2f} err={err/1e6:.0f}ms", flush=True)
        return Gst.PadProbeReturn.OK

    def run(self):
        policy = self.a.timing_policy
        if policy == 'restamp':
            # Default: map the source onto the local clock (+2 grains). The probe
            # fires the announce itself on offset-lock.
            self.sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, self._restamp)
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
                self.sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, self._restamp)
            if self.a.announce_on_lock and self.repair_url and policy in ('align', 'preserve'):
                threading.Thread(target=self._announce, daemon=True).start()
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error',
                    lambda b, m: (sys.stderr.write(f'ERR {m.parse_error()}\n'), self.loop.quit()))
        bus.connect('message::eos',
                    lambda b, m: (sys.stderr.write('EOS\n'), self.loop.quit()))
        self.pipe.set_state(Gst.State.PLAYING)
        print(f'contribution_core running: {self.a.label} -> {self.a.flow_id}', flush=True)
        try:
            self.loop.run()
        finally:
            self.pipe.set_state(Gst.State.NULL)
            sys.exit(1)  # abnormal by definition; supervisor restarts (idle 404 loop is cheap)
