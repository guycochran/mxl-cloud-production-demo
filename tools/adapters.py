#!/usr/bin/env python3
"""SourceAdapters — the per-transport FRONT ENDS of the contribution seam.

Each adapter produces only its launch fragment + routing metadata; the identical
conform/restamp/mxlsink/announce back half lives in contribution_core.py. Adding a
new contribution transport = adding one class here, nothing in the core.

See docs/CONTRIBUTION-SEAM.md.
"""
from contribution_core import SourceAdapter


# -- shared decode fragment: rtspsrc -> h264 depay/parse -> frame-threaded decode --
# Explicit avdec_h264 (NOT uridecodebin): autoplugging picked a decoder that couldn't
# hold 30fps (steady ~1s cadence re-syncs). FINDINGS §3/§8.
def _rtsp_h264_front(url: str, latency_ms: int) -> str:
    # Ends at the decoded queue; the core adds `videorate ! videoscale ! videoconvert
    # ! <CANON_CAPS>`. videorate lives in the CORE conform stage, not here, so it is
    # applied exactly once (a double videorate is a passthrough but not faithful).
    return (f'rtspsrc location={url} latency={latency_ms} protocols=tcp name=src '
            f'! rtph264depay ! h264parse ! avdec_h264 max-threads=4 thread-type=frame '
            f'! queue max-size-buffers=8 ')


class SrtGuestAdapter(SourceAdapter):
    """Open contribution: anyone's SRT lands on mediamtx (streamid publish:guestN),
    exposed as an RTSP path; conform whatever arrives (videoscale letterboxes odd
    aspect ratios, videorate reconciles any framerate) to canonical v210."""
    def __init__(self, path: str, flow_id: str, label: str, latency_ms: int = 200,
                 rtsp_host: str = '172.17.0.1'):
        self.path = path
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self._url = f'rtsp://{rtsp_host}:8554/{path}'
        self.description = f'contributor SRT ingest ({path})'

    def source_fragment(self) -> str:
        return _rtsp_h264_front(self._url, self.latency_ms)


class RtspCamAdapter(SourceAdapter):
    """Studio PTZ camera (H.264 over RTSP). videorate reconciles 30000/1001 -> 30/1
    (the v210 caps intermittently fail to negotiate on bare 29.97 — documented crash)."""
    def __init__(self, url: str, flow_id: str, label: str = 'CAM Live',
                 latency_ms: int = 200):
        self._url = url
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self.description = 'low-latency cam ingest'

    def source_fragment(self) -> str:
        return _rtsp_h264_front(self._url, self.latency_ms)


class MakitoAdapter(SourceAdapter):
    """Static SDI shot via Haivision Makito X4, slot 3.
    NOTE: the shipped cam2_ingest decodes H.264 here (rtph264depay/avdec_h264),
    identical front end to RtspCamAdapter — kept as its own class only for the
    distinct label/description/diag cadence. If a future Makito profile sends
    HEVC, swap the depay/parse/decode to the h265 trio."""
    def __init__(self, url: str, flow_id: str, label: str = 'CAM 2 Live',
                 latency_ms: int = 200):
        self._url = url
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self.description = 'Makito X4 static cam ingest'

    def source_fragment(self) -> str:
        return _rtsp_h264_front(self._url, self.latency_ms)


class ZoomIsoMxlAdapter(SourceAdapter):
    """ZoomISO Cloud -> MXL. STUB: wire when the beta lands (liminalet.com/zoomiso-cloud).

    ZoomISO Cloud conforms remote Zoom signals to NDI/SRT AND the MXL protocol for
    cloud production. If it emits domain-canonical v210 grains, this bypasses decode
    AND conform entirely — the whole point of is_native_mxl.

    *** DO NOT ASSUME — MEASURE ON FIRST BETA CONTACT (docs/CONTRIBUTION-SEAM.md §4): ***
      1. ONE MXL flow per participant, or a single composite? (=> N slots vs 1)
      2. Is its grain cadence ALREADY domain-aligned (skip restamp) or does it need
         the +2-grain restamp like any other source? If aligned, we may set a core
         flag to no-op the probe; if not, the standard restamp applies unchanged.
      3. Does it arrive cross-host via a fabrics proxy (=> sockaddr patch, patch-target-ip.py)?
    NOTE: this is ZoomISO *Cloud* (MXL-native). It is NOT the legacy local-ZoomISO
    OSC path (port 9091 / /zoomosc/) in CLAUDE.md — do not carry those assumptions over.

    Properties are deliberately orthogonal (contribution_core.SourceAdapter):
      needs_conform=False  — it emits v210 grains, skip the conform stage.
      timing_policy='align' — PROVISIONAL. "Native MXL" does NOT mean "already on my
        clock." align logs "assumed, not verified" and skips restamp; if the beta
        shows a foreign clock, flip to 'restamp' (one line). MEASURE first (see above).
    """
    needs_conform = False
    timing_policy = 'align'

    def __init__(self, source_flow_id: str, flow_id: str, label: str = 'Zoom Guest',
                 domain: str = '/mxl-domain'):
        self.source_flow_id = source_flow_id   # the flow ZoomISO Cloud publishes
        self.flow_id = flow_id                  # our selector-slot flow
        self.label = label
        self._domain = domain
        self.description = 'ZoomISO Cloud MXL ingest'

    def source_fragment(self) -> str:
        # Provisional: read the ZoomISO-published grains straight from the domain.
        # Exact element/props TBD against a real beta flow (see class docstring).
        return (f'mxlsrc domain={self._domain} flow-id={self.source_flow_id} '
                f'name=src ! queue max-size-buffers=8 ')
