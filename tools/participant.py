#!/usr/bin/env python3
"""Participant — one contributor, both essences (v0.3 Phase 3).

A guest contributes VIDEO and AUDIO together, but each essence runs in its OWN
process (one-process-per-flow: a wedged audio decode must never take video down —
see docs/AV-CONTRIBUTION-v0.3.md §3b). So Participant is deliberately a DESCRIPTOR
+ BUILDER, not a runner: it resolves a guest's two adapters from the facility
manifest and hands back the two leg definitions, which a supervisor (bring-up /
quickstart / guest_participant.py) launches as separate processes.

    p = Participant.guest(1)          # resolves from config/facility.json
    p.video_adapter()                 # SrtGuestAdapter  -> guest1 video flow
    p.audio_adapter()                 # AudioGuestAdapter -> guest1 audio flow
    p.legs()                          # [{essence, tool, argv, label}, ...]

Why a builder and not `p.run()`: ContributionCore.run() blocks on the GLib loop,
so a single process can host exactly one leg. Pairing them in-process would couple
their failure domains — the opposite of what we want.
"""
import os
import sys

# NOTE: adapters import gi/GStreamer at module load, so we import them LAZILY inside
# video_adapter()/audio_adapter() only. legs()/video_flow()/audio_flow() are pure
# data (launch commands + UUIDs) and must work with no GStreamer — that's how a
# supervisor on a bare box composes the launch without importing the media stack.


def _vflow(name, fallback):
    try:
        from facility import flow_uuid
        return flow_uuid('video', name)
    except Exception:
        return fallback


def _aflow(name, fallback):
    try:
        from facility import flow_uuid
        return flow_uuid('audio', name)
    except Exception:
        return fallback


# baked-in fallbacks mirror the guest flows in config/facility.json
_V_FALLBACK = {'guest1': '9e111e00-aaaa-4bbb-8ccc-000000000001',
               'guest2': '9e222e00-aaaa-4bbb-8ccc-000000000001'}
_A_FALLBACK = {'guest1': 'a1111e00-aaaa-4bbb-8ccc-000000000001',
               'guest2': 'a2222e00-aaaa-4bbb-8ccc-000000000001'}


class Participant:
    """A contributor's paired A/V contribution. `key` is the manifest guest key
    ('guest1'/'guest2'); `path` is the mediamtx SRT/RTSP path the phone publishes to
    (defaults to the key); `label` is the human/switcher label (defaults 'Guest N')."""

    def __init__(self, key, path=None, label=None,
                 video_latency_ms=200, audio_latency_ms=300,
                 host=None, transport=None):
        self.key = key
        self.path = path or key
        self.label = label or f'Guest {key[-1]}'
        self.video_latency_ms = video_latency_ms
        self.audio_latency_ms = audio_latency_ms
        # mediamtx host for both SRT read + RTSP. Default the docker-bridge gateway.
        self.host = host or os.environ.get('MXL_GUEST_HOST', '172.17.0.1')
        # 'srt-direct' (default, hardware-proven) | 'rtsp' (legacy)
        self.transport = transport or os.environ.get('MXL_GUEST_TRANSPORT', 'srt-direct')

    @classmethod
    def guest(cls, n, **kw):
        return cls(f'guest{n}', **kw)

    def video_flow(self):
        return _vflow(self.key, _V_FALLBACK.get(self.key, ''))

    def audio_flow(self):
        return _aflow(self.key, _A_FALLBACK.get(self.key, ''))

    def video_adapter(self):
        if self.transport == 'rtsp':
            from adapters import SrtGuestAdapter  # legacy rtspsrc
            return SrtGuestAdapter(path=self.path, flow_id=self.video_flow(),
                                   label=self.label, latency_ms=self.video_latency_ms,
                                   rtsp_host=self.host)
        from adapters import SrtGuestVideoAdapter  # default srtsrc!tsdemux
        return SrtGuestVideoAdapter(path=self.path, flow_id=self.video_flow(),
                                    label=self.label, latency_ms=self.video_latency_ms,
                                    srt_host=self.host)

    def audio_adapter(self):
        if self.transport == 'rtsp':
            from adapters import AudioGuestAdapter  # legacy rtspsrc!decodebin
            return AudioGuestAdapter(path=self.path, flow_id=self.audio_flow(),
                                     label=f'{self.label} Audio',
                                     latency_ms=self.audio_latency_ms, rtsp_host=self.host)
        from adapters import SrtGuestAudioAdapter  # default srtsrc!tsdemux!aac
        return SrtGuestAudioAdapter(path=self.path, flow_id=self.audio_flow(),
                                    label=f'{self.label} Audio',
                                    latency_ms=self.audio_latency_ms, srt_host=self.host)

    def legs(self):
        """The two legs a supervisor should launch as SEPARATE processes.
        argv is positional to match the existing guest_ingest.py / guest_audio.py
        CLI contracts, so a supervisor can keep calling those tools unchanged."""
        return [
            {'essence': 'video', 'tool': 'guest_ingest.py',
             'argv': [self.path, self.video_flow(), self.label,
                      str(self.video_latency_ms)],
             'label': self.label},
            {'essence': 'audio', 'tool': 'guest_audio.py',
             'argv': [self.path, self.audio_flow(), f'{self.label} Audio',
                      str(self.audio_latency_ms)],
             'label': f'{self.label} Audio'},
        ]


if __name__ == '__main__':
    # `participant.py guest1` — print the two leg launch commands (for a supervisor
    # or for eyeballing). Runs nothing itself.
    key = sys.argv[1] if len(sys.argv) > 1 else 'guest1'
    p = Participant(key)
    print(f'Participant {p.label}: path={p.path}')
    for leg in p.legs():
        print(f"  [{leg['essence']:5}] python3 {leg['tool']} " + ' '.join(
            (f'"{a}"' if ' ' in a else a) for a in leg['argv']))
