#!/usr/bin/env python3
"""Guest AUDIO ingest — thin adapter (v0.3).

Pulls a contributor's audio and writes it as an MXL audio flow for the program
mixer, with the cadence-preserving restamp. The audio twin of guest_ingest.py.

TRANSPORT (MXL_GUEST_TRANSPORT): 'srt-direct' (DEFAULT) reads straight from mediamtx
over SRT (srtsrc ! tsdemux ! aac) — hardware-proven, no RTSP-two-track flap (see
docs/AV-CONTRIBUTION-v0.3.md §8). 'rtsp' keeps the legacy rtspsrc ! decodebin path
(fine for audio-only sources).

Usage: guest_audio.py <path> [flow-uuid] [label] [jitterbuffer_ms]
  e.g. guest_audio.py guest1 a1111e00-aaaa-4bbb-8ccc-000000000001 "Guest 1 Audio"

Supervised: exits on error/EOS, the runner respawns; while the contributor is
offline the source 404s and we just retry — the flow is absent, and the mixer
tolerates that. Source host via MXL_GUEST_HOST (default 172.17.0.1).
"""
import os
import sys

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
TRANSPORT = os.environ.get('MXL_GUEST_TRANSPORT', 'srt-direct')
# MXL_AUDIO_RTSP_HOST kept for back-compat; MXL_GUEST_HOST is the unified knob.
HOST = os.environ.get('MXL_GUEST_HOST', os.environ.get('MXL_AUDIO_RTSP_HOST', '172.17.0.1'))
# srt-listen: the audio leg of the A/V fan-out reads the local AAC split (own port on
# 127.0.0.1). See tools/guest_av_listen.sh + SrtListenerGuestAudioAdapter.
LISTEN_PORT = int(os.environ.get('MXL_GUEST_LISTEN_PORT', '8890'))
LISTEN_HOST = os.environ.get('MXL_GUEST_LISTEN_HOST', '0.0.0.0')

if TRANSPORT == 'rtsp':
    from adapters import AudioGuestAdapter  # legacy rtspsrc ! decodebin
    adapter = AudioGuestAdapter(path=PATH, flow_id=DST, label=LABEL,
                                latency_ms=JITTER_MS, rtsp_host=HOST)
elif TRANSPORT == 'srt-listen':
    from adapters import SrtListenerGuestAudioAdapter  # direct: srtsrc mode=listener ! aac
    adapter = SrtListenerGuestAudioAdapter(path=PATH, flow_id=DST, label=LABEL,
                                           latency_ms=JITTER_MS, listen_port=LISTEN_PORT,
                                           listen_host=LISTEN_HOST)
else:
    from adapters import SrtGuestAudioAdapter  # default: srtsrc ! tsdemux ! aac (via mediamtx)
    adapter = SrtGuestAudioAdapter(path=PATH, flow_id=DST, label=LABEL,
                                   latency_ms=JITTER_MS, srt_host=HOST)

ContributionCore(adapter, diag_every=300).run()
