#!/usr/bin/env python3
"""Contributor ("guest") SRT ingest for the MXL switcher — thin adapter.

Anyone can push SRT to mediamtx (streamid publish:guest1 / guest2); this conforms
whatever arrives to canonical 1080p30 v210 and writes it into the domain as a
cuttable selector input, with the cadence-preserving +2-grain restamp and the
hands-off /api/mxl/repair announce on first locked frame.

All of that logic now lives in contribution_core.py + adapters.py; this file is the
thin front door that preserves the original CLI contract (bring-up-mxl.sh / the
supervisor call it positionally).

Usage: guest_ingest.py <path> <flow-uuid> <label> [jitterbuffer_ms]
   eg: guest_ingest.py guest1 9e111e00-aaaa-4bbb-8ccc-000000000001 "Guest 1"
"""
import sys

from adapters import SrtGuestAdapter
from contribution_core import ContributionCore

PATH = sys.argv[1] if len(sys.argv) > 1 else 'guest1'
DST = sys.argv[2] if len(sys.argv) > 2 else '9e111e00-aaaa-4bbb-8ccc-000000000001'
LABEL = sys.argv[3] if len(sys.argv) > 3 else 'Guest 1'
JITTER_MS = int(sys.argv[4]) if len(sys.argv) > 4 else 200

ContributionCore(
    SrtGuestAdapter(path=PATH, flow_id=DST, label=LABEL, latency_ms=JITTER_MS),
    diag_every=300,
).run()
