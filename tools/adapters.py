#!/usr/bin/env python3
"""SourceAdapters — the per-transport FRONT ENDS of the contribution seam.

Each adapter produces only its launch fragment + routing metadata; the identical
conform/restamp/mxlsink/announce back half lives in contribution_core.py. Adding a
new contribution transport = adding one class here, nothing in the core.

See docs/CONTRIBUTION-SEAM.md.
"""
import re

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


class AudioGuestAdapter(SourceAdapter):
    """Contributor AUDIO leg: pulls a guest's audio from mediamtx over RTSP and the
    core conforms it to F32LE/48k/2ch and restamps it as an MXL audio flow for the
    program mixer. The audio twin of SrtGuestAdapter — one participant runs both.

    essence='audio' makes the core use the audio conform caps + the duration-
    accumulate restamp. Front end uses decodebin (audio codec varies by publisher);
    default host is the facility's internal/VNet address, overridable.

    Byte-for-byte reproduces tools/guest_audio.py's shipped pipeline (verified by
    tests/test_launch_parity.py)."""
    essence = 'audio'

    def __init__(self, path: str, flow_id: str, label: str, latency_ms: int = 300,
                 rtsp_host: str = '10.0.0.5'):
        self.path = path
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self._url = f'rtsp://{rtsp_host}:8554/{path}'
        self.description = f'contributor audio ({path})'

    def source_fragment(self) -> str:
        # decodebin: the publisher's audio codec (AAC/Opus/…) varies; the core adds
        # `audioconvert ! audioresample ! <CANON_AUDIO_CAPS> ! queue`.
        return (f'rtspsrc location={self._url} latency={self.latency_ms} protocols=tcp '
                f'name=src ! decodebin ')


# -- SRT-direct front ends: read the MPEG-TS straight from mediamtx over SRT and ----
# demux with tsdemux, instead of mediamtx's RTSP re-pack. WHY (proven on HW Oct 2026):
# `rtspsrc ! decodebin` on a 2-track (video+audio) source leaves the UNUSED track
# not-linked -> the ingest flaps (restart loop, re-anchoring -> lip-sync unmeasurable).
# tsdemux exposes clean separate pads and tolerates an unlinked one, so each leg reads
# ONE SRT stream and taps only its essence — 0 restarts in testing. Drops the RTSP hop
# too. mediamtx stays the SRT front door (streamid auth + serves SRT read + WebRTC).
#   read URL: srt://<host>:8890?streamid=read:<path>&latency=<ms>


class SrtGuestVideoAdapter(SourceAdapter):
    """Guest VIDEO via SRT-direct: srtsrc ! tsdemux ! h264 decode. The core appends
    the canonical v210 conform + restamp + mxlsink. Replaces the rtsp guest video
    front end for A/V sources (see _srt_src note). mediamtx serves the SRT read."""
    def __init__(self, path: str, flow_id: str, label: str, latency_ms: int = 300,
                 srt_host: str = '172.17.0.1', srt_port: int = 8890):
        self.path = path
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self._uri = f'srt://{srt_host}:{srt_port}?streamid=read:{path}&latency={latency_ms}'
        self.description = f'contributor SRT-direct video ({path})'

    def source_fragment(self) -> str:
        # tsdemux exposes video + audio pads; we tap only video (the audio pad stays
        # unlinked, which tsdemux tolerates — unlike rtspsrc). Core adds videorate/
        # videoscale/videoconvert/v210.
        return (f'srtsrc uri="{self._uri}" ! tsdemux name=d d. '
                f'! queue ! h264parse ! avdec_h264 max-threads=4 thread-type=frame '
                f'! queue max-size-buffers=8 ')


# SRT passphrase for a PUBLIC srt-listen listener (MXL_GUEST_SRT_PASSPHRASE). libsrt wants
# 10-79 chars; we also restrict it to URL-unreserved characters so it survives a GStreamer
# launch string / gst-launch argv AND a caller's `srt://...&passphrase=` URL unescaped
# (ffmpeg URL-decodes '%', '+' etc., which silently yields a different key).
# With a passphrase set, srtsrc rejects unencrypted and wrong-key callers at handshake.
SRT_PASSPHRASE_RE = re.compile(r'^[A-Za-z0-9._~-]{10,79}$')


def srt_listen_passphrase(value):
    """Validate an srt-listen passphrase. '' / None -> '' (listener stays open)."""
    value = (value or '').strip()
    if value and not SRT_PASSPHRASE_RE.match(value):
        raise ValueError('SRT passphrase must be 10-79 chars of A-Z a-z 0-9 . _ ~ - '
                         '(e.g. `openssl rand -hex 16`)')
    return value


class SrtListenerGuestAdapter(SourceAdapter):
    """Guest VIDEO via SRT-DIRECT-LISTEN: the ingest IS the SRT listener — the
    contributor's SRT caller lands straight on srtsrc, no mediamtx in the contribution
    path. This is the "spin up and ingest SRT to mix/switch, nothing in the way" path.

    WHY (proven on HW Oct 6 2026): the mediamtx round-trip (publish TO mediamtx, then
    srtsrc reads BACK over SRT with streamid=read:) is fragile — the read-back leg
    throws `srtsrc: streaming stopped, reason error (-5)` on cellular/jittery sources
    and restart-loops, so the flow never latches a stable writer. Reading the caller
    DIRECTLY (srtsrc mode=listener) ran a real 1080p camera at a steady 30fps, 0 drops,
    0 restarts — the extra hop was the entire problem. mediamtx stays only for the
    WebRTC monitor, which reads the MXL flow OUT (never into the contribution path).

    One listener owns one UDP port, so each guest slot binds its own SRT port
    (8890 + slot offset). The contributor's deep-link/QR points straight here."""
    def __init__(self, path: str, flow_id: str, label: str, latency_ms: int = 300,
                 listen_port: int = 8890, listen_host: str = '0.0.0.0', passphrase: str = ''):
        self.path = path
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        # Optional SRT encryption on the listener (validated; never put in the description).
        self._passphrase = srt_listen_passphrase(passphrase)
        # Default binds all interfaces so the public caller reaches us (one port per
        # guest). The A/V fan-out (guest_av_listen) binds 127.0.0.1 instead: there the
        # public listener is the fan-out, and this leg only reads the local split.
        self._uri = (f'srt://{listen_host}:{listen_port}'
                     f'?mode=listener&latency={latency_ms}')
        self.description = f'contributor SRT-direct-listen video ({path} :{listen_port})'

    def source_fragment(self) -> str:
        # Same caps-selective demux link as SrtGuestVideoAdapter: h264parse (sink caps
        # video/x-h264) sits directly on the tsdemux SOMETIMES-pad so parse_launch binds
        # the video ES regardless of TS track order.
        #
        # ⚠️ The post-decode queue MUST be leaky=downstream, NOT a plain bounded queue.
        # The core appends a heavy synchronous conform (videorate 60→30 + videoscale +
        # v210 10-bit + mxlsink). When that tail stalls for even a few frames, a plain
        # queue fills, backpressures avdec/srtsrc, and srtsrc's SRT RECEIVE buffer then
        # overflows → "streaming stopped, reason error (-5)" and a restart loop. (A bare
        # srtsrc→decode→fakesink ran this exact camera at a steady 30fps/0-drops; adding
        # the conform tail is what reintroduced -5.) A downstream-leaky queue decouples
        # the SRT receiver from the conform: if the conform can't keep up it DROPS the
        # oldest decoded frame instead of back-pressuring the network leg, so srtsrc keeps
        # draining the socket and never trips -5. Sized by time (400ms) so it tolerates a
        # conform hiccup without unbounded latency. (Diagnosed on HW Oct 6 2026.)
        pp = f' passphrase="{self._passphrase}"' if self._passphrase else ''
        return (f'srtsrc uri="{self._uri}"{pp} ! tsdemux name=d d. '
                f'! h264parse ! avdec_h264 max-threads=4 thread-type=frame '
                f'! queue leaky=downstream max-size-time=400000000 max-size-buffers=0 '
                f'max-size-bytes=0 ')


class SrtListenerGuestAudioAdapter(SourceAdapter):
    """Guest AUDIO via SRT-DIRECT-LISTEN: the audio twin of SrtListenerGuestAdapter.
    srtsrc mode=listener ! tsdemux taps the AAC track; essence='audio' so the core
    uses the F32LE/48k conform + duration-accumulate restamp. Used by the A/V fan-out
    (guest_av_listen) so a single contributor SRT stream feeds BOTH a video and an
    audio MXL flow — the fan-out splits the one listener's TS to a local video leg and
    a local audio leg, each bound on 127.0.0.1 (listen_host)."""
    essence = 'audio'

    def __init__(self, path: str, flow_id: str, label: str, latency_ms: int = 300,
                 listen_port: int = 8890, listen_host: str = '0.0.0.0'):
        self.path = path
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self._uri = (f'srt://{listen_host}:{listen_port}'
                     f'?mode=listener&latency={latency_ms}')
        self.description = f'contributor SRT-direct-listen audio ({path} :{listen_port})'

    def source_fragment(self) -> str:
        # Mirror the video listener on BOTH counts (HW Oct 6 2026):
        # 1) aacparse (sink caps audio/mpeg) sits DIRECTLY on the tsdemux SOMETIMES-pad —
        #    no ANY-caps queue between them — so parse_launch binds the AUDIO ES regardless
        #    of TS track order (a `queue` there, ANY caps, could grab the video pad).
        # 2) a leaky=downstream queue AFTER decode decouples srtsrc from the F32LE conform:
        #    if the conform stalls (or the fan-out leg reconnects), DROP the oldest decoded
        #    audio rather than back-pressure srtsrc into an SRT receive overflow → the same
        #    "streaming stopped, reason error (-5)" restart-loop the video leg hit. Observed
        #    on the A/V fan-out's audio leg: plain queue → srtsrc -5 loop; leaky → stable.
        return (f'srtsrc uri="{self._uri}" ! tsdemux name=d d. '
                f'! aacparse ! avdec_aac '
                f'! queue leaky=downstream max-size-time=400000000 max-size-buffers=0 '
                f'max-size-bytes=0 ')


class SrtGuestAudioAdapter(SourceAdapter):
    """Guest AUDIO via SRT-direct: srtsrc ! tsdemux ! aac decode. essence='audio' so
    the core uses the F32LE/48k conform + the duration-accumulate restamp. The clean
    fix for the rtspsrc-on-A/V flap (proven 0-restart on HW)."""
    essence = 'audio'

    def __init__(self, path: str, flow_id: str, label: str, latency_ms: int = 300,
                 srt_host: str = '172.17.0.1', srt_port: int = 8890):
        self.path = path
        self.flow_id = flow_id
        self.label = label
        self.latency_ms = latency_ms
        self._uri = f'srt://{srt_host}:{srt_port}?streamid=read:{path}&latency={latency_ms}'
        self.description = f'contributor SRT-direct audio ({path})'

    def source_fragment(self) -> str:
        # tap only the audio pad from tsdemux; video pad stays unlinked (tolerated).
        # Core adds audioconvert/audioresample/F32LE/queue.
        return (f'srtsrc uri="{self._uri}" ! tsdemux name=d d. '
                f'! queue ! aacparse ! avdec_aac ')


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
      needs_conform  — False if ZoomISO already emits domain-canonical v210 grains
                       (expected). Set True only if the beta shows a non-canonical
                       format we must conform.
      timing_policy  — 'align' (PROVISIONAL default): "native MXL" does NOT mean
                       "on my clock." align logs "assumed, not verified" + skips
                       restamp. If the beta shows a foreign clock → pass
                       timing_policy='restamp' (one kwarg). MEASURE first.

    BOTH FLOW SHAPES are handled so beta day is a config flip, not a rewrite — use
    the `zoomiso_adapters()` factory below rather than constructing by hand.
    """
    needs_conform = False

    def __init__(self, source_flow_id, flow_id, label='Zoom Guest',
                 domain='/mxl-domain', timing_policy='align', needs_conform=False):
        self.source_flow_id = source_flow_id   # the flow ZoomISO Cloud publishes
        self.flow_id = flow_id                  # our selector-slot flow
        self.label = label
        self._domain = domain
        self.timing_policy = timing_policy      # 'align' | 'restamp' | 'preserve' — set at beta
        self.needs_conform = needs_conform
        self.description = 'ZoomISO Cloud MXL ingest'

    def source_fragment(self) -> str:
        # Read the ZoomISO-published grains straight from the domain. If the beta
        # shows the flow arrives cross-host via a fabrics proxy, the source is still
        # mxlsrc on the LOCAL domain — the proxy lands it here first (and may need
        # patch-target-ip.py for non-routed nets; see CONTRIBUTION-SEAM.md §4).
        # NOTE: mxlSRC uses `video-flow-id` (also audio-flow-id/data-flow-id), NOT the
        # `flow-id` that mxlSINK uses — verified via gst-inspect in the dry-run harness.
        return (f'mxlsrc domain={self._domain} video-flow-id={self.source_flow_id} '
                f'name=src ! queue max-size-buffers=8 ')


# ── ZoomISO flow-shape factory — the two possibilities, one call ────────────────
# Beta-day unknown: does ZoomISO Cloud emit ONE MXL flow per participant, or ONE
# composite flow of the whole gallery? We don't guess — we handle both, and pick
# the mode once `mxl-info` shows us the real output (see docs/ZOOMISO-BETA-RUNBOOK.md).
#
#   mode='per_participant': N source flows → N adapters → N selector slots
#                           (each participant is independently cuttable — the ideal).
#   mode='composite'      : 1 source flow → 1 adapter → 1 slot (switch inside Zoom,
#                           or we add a layout/crop stage later to split it).
#
# GUEST_* flow UUIDs reuse the guest-slot convention so the selector/watcher wiring
# is unchanged — ZoomISO participants simply occupy guest slots.
def zoomiso_adapters(source_flow_ids, slot_flow_ids, mode='per_participant',
                     domain='/mxl-domain', timing_policy='align', label_prefix='Zoom'):
    """Return the list of SourceAdapters for a ZoomISO Cloud session.

    source_flow_ids : the flow UUID(s) ZoomISO publishes (from mxl-info at beta).
    slot_flow_ids   : our selector-slot flow UUIDs to map onto (e.g. the guest slots).
    mode            : 'per_participant' (N→N) or 'composite' (first id only → 1 slot).
    timing_policy   : set 'restamp' here if the beta shows a foreign clock.
    """
    if mode == 'composite':
        return [ZoomIsoMxlAdapter(source_flow_ids[0], slot_flow_ids[0],
                                  label=f'{label_prefix} Program', domain=domain,
                                  timing_policy=timing_policy)]
    if mode != 'per_participant':
        raise ValueError(f"unknown ZoomISO mode '{mode}' (per_participant|composite)")
    n = min(len(source_flow_ids), len(slot_flow_ids))
    return [ZoomIsoMxlAdapter(source_flow_ids[i], slot_flow_ids[i],
                              label=f'{label_prefix} {i+1}', domain=domain,
                              timing_policy=timing_policy)
            for i in range(n)]
