#!/usr/bin/env python3
"""Camera 2 (static SDI shot via Haivision Makito X4) ingest — thin adapter.

Same canonical back half as cam1/guest (contribution_core.py); this is the front
door that preserves cam2's CLI contract, flow UUID (slot 3), and diag cadence.
Decodes H.264 (the shipped Makito profile), videorate reconciles 30000/1001 -> 30/1.

Called by scripts/bring-up-mxl.sh with NO args (URL/flow hardcoded here, as before).
Usage: cam2_ingest.py [rtsp_url] [jitterbuffer_ms]
"""
import sys

from adapters import MakitoAdapter
from contribution_core import ContributionCore

URL = sys.argv[1] if len(sys.argv) > 1 else 'rtsp://admin:Password@172.17.0.1:8554/cam2'
JITTER_MS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
DST = 'ca222e00-aaaa-4bbb-8ccc-000000000001'   # CAM 2 Live (selector slot 3)

ContributionCore(
    MakitoAdapter(url=URL, flow_id=DST, label='CAM 2 Live', latency_ms=JITTER_MS),
    diag_every=150,
).run()
