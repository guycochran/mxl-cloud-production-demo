#!/usr/bin/env python3
"""Guest AUDIO ingest — thin adapter (v0.3 Phase 2).

Pulls a contributor's audio from mediamtx over RTSP and writes it as an MXL audio
flow for the program mixer, with the cadence-preserving restamp. The audio twin of
cam_ingest.py: all logic lives in contribution_core.py (essence='audio') + the
AudioGuestAdapter in adapters.py. Re-expressing the old standalone pipeline through
the core means the audio path now carries the same hardened conform/restamp/announce
as every video ingest, in ONE place.

Usage: guest_audio.py <path> [flow-uuid] [label] [jitterbuffer_ms]
  e.g. guest_audio.py guest1 a1111e00-aaaa-4bbb-8ccc-000000000001 "Guest 1 Audio"

Supervised: exits on error/EOS, the runner respawns; while the contributor is
offline the RTSP 404s and we just retry — the flow is absent, and the mixer
tolerates that. Source host defaults to the facility VNet address; override with
MXL_AUDIO_RTSP_HOST or by passing a full path. Byte-identical to the pre-Phase-2
pipeline (tests/test_launch_parity.py).
"""
import os
import sys

from adapters import AudioGuestAdapter
from contribution_core import ContributionCore

PATH = sys.argv[1] if len(sys.argv) > 1 else 'guest1'
# flow: explicit arg > facility manifest 'guest1' audio > baked-in fallback
if len(sys.argv) > 2:
    DST = sys.argv[2]
else:
    try:
        from facility import flow_uuid
        DST = flow_uuid('audio', 'guest1')
    except Exception:
        DST = 'a1111e00-aaaa-4bbb-8ccc-000000000001'
LABEL = sys.argv[3] if len(sys.argv) > 3 else 'Guest 1 Audio'
JITTER_MS = int(sys.argv[4]) if len(sys.argv) > 4 else 300
RTSP_HOST = os.environ.get('MXL_AUDIO_RTSP_HOST', '10.0.0.5')

ContributionCore(
    AudioGuestAdapter(path=PATH, flow_id=DST, label=LABEL,
                      latency_ms=JITTER_MS, rtsp_host=RTSP_HOST),
    diag_every=300,
).run()
