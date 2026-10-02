#!/usr/bin/env python3
"""Camera 1 (studio PTZ, H.264 over RTSP) ingest — thin adapter.

Low-latency cam -> canonical v210 -> "CAM Live" (selector slot 0), with the
cadence-preserving +2-grain restamp that makes a remote source instantly cuttable
against local flows. Logic lives in contribution_core.py + adapters.py.

Called by scripts/bring-up-mxl.sh with NO args (URL/flow hardcoded here, as before).
Usage: cam_ingest.py [rtsp_url] [jitterbuffer_ms]
"""
import sys

from adapters import RtspCamAdapter
from contribution_core import ContributionCore

URL = sys.argv[1] if len(sys.argv) > 1 else 'rtsp://admin:Password@172.17.0.1:8554/cam1'
JITTER_MS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
DST = 'ca111e00-aaaa-4bbb-8ccc-000000000001'   # CAM Live (selector slot 0)

ContributionCore(
    RtspCamAdapter(url=URL, flow_id=DST, label='CAM Live', latency_ms=JITTER_MS),
    diag_every=150,
).run()
