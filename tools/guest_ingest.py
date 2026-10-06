#!/usr/bin/env python3
"""Contributor ("guest") video ingest for the MXL switcher — thin adapter.

Anyone can push SRT to mediamtx (streamid publish:guest1 / guest2); this conforms
whatever arrives to canonical 1080p30 v210 and writes it into the domain as a
cuttable selector input, with the cadence-preserving +2-grain restamp and the
hands-off /api/mxl/repair announce on first locked frame.

TRANSPORT (MXL_GUEST_TRANSPORT): 'srt-direct' (DEFAULT) reads the stream straight
from mediamtx over SRT (srtsrc ! tsdemux) — this is the hardware-proven path; the
RTSP re-pack leaves a 2-track source's unused track not-linked and flaps (see
docs/AV-CONTRIBUTION-v0.3.md §8). 'rtsp' keeps the legacy rtspsrc path for
audio-only sources or debugging.

All logic lives in contribution_core.py + adapters.py; this file is the thin front
door that preserves the original CLI contract (bring-up-mxl.sh / the supervisor call
it positionally).

Usage: guest_ingest.py <path> <flow-uuid> <label> [jitterbuffer_ms]
   eg: guest_ingest.py guest1 9e111e00-aaaa-4bbb-8ccc-000000000001 "Guest 1"
"""
import os
import sys

from contribution_core import ContributionCore

PATH = sys.argv[1] if len(sys.argv) > 1 else 'guest1'


def _guest1_default():
    try:
        from facility import flow_uuid
        return flow_uuid('video', 'guest1')
    except Exception:
        return '9e111e00-aaaa-4bbb-8ccc-000000000001'


DST = sys.argv[2] if len(sys.argv) > 2 else _guest1_default()
LABEL = sys.argv[3] if len(sys.argv) > 3 else 'Guest 1'
JITTER_MS = int(sys.argv[4]) if len(sys.argv) > 4 else 200
TRANSPORT = os.environ.get('MXL_GUEST_TRANSPORT', 'srt-direct')
HOST = os.environ.get('MXL_GUEST_HOST', '172.17.0.1')  # mediamtx host (srt + rtsp)
# srt-listen: the ingest binds its OWN SRT port and the contributor's caller lands
# straight on it — mediamtx is out of the contribution path entirely. One port per
# guest (default 8890 + slot offset). See SrtListenerGuestAdapter for the HW rationale.
LISTEN_PORT = int(os.environ.get('MXL_GUEST_LISTEN_PORT', '8890'))

if TRANSPORT == 'rtsp':
    from adapters import SrtGuestAdapter  # legacy rtspsrc path
    adapter = SrtGuestAdapter(path=PATH, flow_id=DST, label=LABEL,
                              latency_ms=JITTER_MS, rtsp_host=HOST)
elif TRANSPORT == 'srt-listen':
    from adapters import SrtListenerGuestAdapter  # direct: srtsrc mode=listener
    adapter = SrtListenerGuestAdapter(path=PATH, flow_id=DST, label=LABEL,
                                      latency_ms=JITTER_MS, listen_port=LISTEN_PORT)
else:
    from adapters import SrtGuestVideoAdapter  # default: srtsrc ! tsdemux (via mediamtx)
    adapter = SrtGuestVideoAdapter(path=PATH, flow_id=DST, label=LABEL,
                                   latency_ms=JITTER_MS, srt_host=HOST)

ContributionCore(adapter, diag_every=300).run()
