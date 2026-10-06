"""Golden-string parity test for the guest SRT launch pipeline.

During the seam refactor we verified (via a stubbed-gi dry run) that the guest launch
string was BYTE-IDENTICAL to the pre-refactor cam/guest ingest. This formalizes that
check: the full gst-launch string for a guest slot is pinned here. If a refactor
changes it, this fails loudly and the diff shows exactly what moved — then you decide
whether the change is intentional (update the golden) or a regression (revert).

The string is built through the REAL ContributionCore + SrtGuestAdapter (stubbed gi),
so it reflects the actual shipped pipeline, not a hand-written copy.
"""
from pathlib import Path

import contribution_core as cc
from adapters import SrtGuestAdapter

REPO = Path(__file__).resolve().parent.parent

# The proven guest pipeline, as shipped. Front end = rtspsrc h264 decode (adapters.py
# _rtsp_h264_front), back half = core conform (videorate/videoscale/videoconvert/v210)
# -> mxlsink. rtsp_host default 172.17.0.1, latency 200, path guest1.
GOLDEN_GUEST_LAUNCH = (
    "rtspsrc location=rtsp://172.17.0.1:8554/guest1 latency=200 protocols=tcp name=src "
    "! rtph264depay ! h264parse ! avdec_h264 max-threads=4 thread-type=frame "
    "! queue max-size-buffers=8 "
    "! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 "
    "! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,"
    "pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709 "
    "! mxlsink name=sink domain=/mxl-domain "
    "flow-id=9e111e00-aaaa-4bbb-8ccc-000000000001 "
    'label="Guest 1" description="contributor SRT ingest (guest1)" '
    'group-hint="Guest1:Video" sync=false'
)


def test_guest_launch_string_is_pinned():
    core = cc.ContributionCore(
        SrtGuestAdapter(
            path="guest1",
            flow_id="9e111e00-aaaa-4bbb-8ccc-000000000001",
            label="Guest 1",
        ),
        repair_url="",
    )
    assert core.pipe._launch == GOLDEN_GUEST_LAUNCH


# The studio-camera path (cam1 RTSP + cam2/Makito) shares the exact H.264 decode
# front end. Pin it too so a refactor can't silently change the proven cam pipeline.
GOLDEN_CAM_LAUNCH = (
    "rtspsrc location=rtsp://172.17.0.1:8554/cam1 latency=200 protocols=tcp name=src "
    "! rtph264depay ! h264parse ! avdec_h264 max-threads=4 thread-type=frame "
    "! queue max-size-buffers=8 "
    "! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 "
    "! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,"
    "pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709 "
    "! mxlsink name=sink domain=/mxl-domain "
    "flow-id=ca111e00-aaaa-4bbb-8ccc-000000000001 "
    'label="CAM Live" description="low-latency cam ingest" '
    'group-hint="CAMLive:Video" sync=false'
)


def test_cam_launch_string_is_pinned():
    from adapters import RtspCamAdapter
    core = cc.ContributionCore(
        RtspCamAdapter(url="rtsp://172.17.0.1:8554/cam1",
                       flow_id="ca111e00-aaaa-4bbb-8ccc-000000000001",
                       label="CAM Live"),
        repair_url="",
    )
    assert core.pipe._launch == GOLDEN_CAM_LAUNCH


# The guest AUDIO leg (v0.3 A/V contribution, Phase 1). essence='audio' makes the
# core use the F32LE/48k/2ch conform + the duration-accumulate restamp. This golden
# string is BYTE-IDENTICAL to the shipped tools/guest_audio.py pipeline — that parity
# is the whole safety argument for routing audio through the core. rtsp_host default
# 10.0.0.5 (the facility VNet address), latency 300, path guest1.
GOLDEN_GUEST_AUDIO_LAUNCH = (
    "rtspsrc location=rtsp://10.0.0.5:8554/guest1 latency=300 protocols=tcp name=src "
    "! decodebin "
    "! audioconvert ! audioresample "
    "! audio/x-raw,format=F32LE,layout=interleaved,rate=48000,"
    "channels=2,channel-mask=(bitmask)0x3 "
    "! queue max-size-buffers=32 "
    "! mxlsink name=sink domain=/mxl-domain "
    "flow-id=a1111e00-aaaa-4bbb-8ccc-000000000001 "
    'label="Guest 1 Audio" description="contributor audio (guest1)" '
    'group-hint="Guest1Audio:Audio" sync=false'
)


def test_guest_audio_launch_string_matches_shipped_guest_audio():
    from adapters import AudioGuestAdapter
    core = cc.ContributionCore(
        AudioGuestAdapter(path="guest1",
                          flow_id="a1111e00-aaaa-4bbb-8ccc-000000000001",
                          label="Guest 1 Audio"),
        repair_url="",
    )
    assert core.pipe._launch == GOLDEN_GUEST_AUDIO_LAUNCH


def test_guest_audio_is_a_thin_shim():
    """Phase 2: guest_audio.py must route through the core, not carry its own
    pipeline. Guards against a regression where someone re-inlines the gst-launch
    (which would re-fork the audio path away from the hardened core)."""
    src = (REPO / "tools" / "guest_audio.py").read_text()
    assert "AudioGuestAdapter" in src and "ContributionCore" in src
    assert "Gst.parse_launch" not in src, "guest_audio.py re-inlined a pipeline"
    assert "mxlsink" not in src, "guest_audio.py still hand-builds the sink"


# SRT-direct guest adapters (v0.3.1): read MPEG-TS straight from mediamtx over SRT and
# demux with tsdemux — fixes the rtspsrc-on-A/V not-linked flap. Proven on HW Oct 2026.
GOLDEN_SRT_VIDEO = (
    'srtsrc uri="srt://172.17.0.1:8890?streamid=read:guestA&latency=300" ! tsdemux name=d d. '
    "! queue ! h264parse ! avdec_h264 max-threads=4 thread-type=frame ! queue max-size-buffers=8 "
    "! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 "
    "! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,"
    "pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709 "
    "! mxlsink name=sink domain=/mxl-domain flow-id=9e111e00-aaaa-4bbb-8ccc-000000000001 "
    'label="Guest 1" description="contributor SRT-direct video (guestA)" '
    'group-hint="Guest1:Video" sync=false'
)
GOLDEN_SRT_AUDIO = (
    'srtsrc uri="srt://172.17.0.1:8890?streamid=read:guestA&latency=300" ! tsdemux name=d d. '
    "! queue ! aacparse ! avdec_aac ! audioconvert ! audioresample "
    "! audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2,channel-mask=(bitmask)0x3 "
    "! queue max-size-buffers=32 "
    "! mxlsink name=sink domain=/mxl-domain flow-id=a1111e00-aaaa-4bbb-8ccc-000000000001 "
    'label="Guest 1 Audio" description="contributor SRT-direct audio (guestA)" '
    'group-hint="Guest1Audio:Audio" sync=false'
)


def test_srt_direct_video_launch_is_pinned():
    from adapters import SrtGuestVideoAdapter
    core = cc.ContributionCore(
        SrtGuestVideoAdapter(path="guestA", flow_id="9e111e00-aaaa-4bbb-8ccc-000000000001",
                             label="Guest 1"),
        repair_url="")
    assert core.pipe._launch == GOLDEN_SRT_VIDEO


def test_srt_direct_audio_launch_is_pinned():
    from adapters import SrtGuestAudioAdapter
    core = cc.ContributionCore(
        SrtGuestAudioAdapter(path="guestA", flow_id="a1111e00-aaaa-4bbb-8ccc-000000000001",
                             label="Guest 1 Audio"),
        repair_url="")
    assert core.pipe._launch == GOLDEN_SRT_AUDIO


# --- SRT-direct-LISTEN adapters (srt-listen): the ingest IS the listener. Video +
#     audio each tap their own srtsrc(listener)!tsdemux. Used by the A/V fan-out.
def test_srt_listen_video_launch_is_pinned():
    from adapters import SrtListenerGuestAdapter
    core = cc.ContributionCore(
        SrtListenerGuestAdapter(path="guest1", flow_id="9e111e00-aaaa-4bbb-8ccc-000000000001",
                                label="Guest 1", listen_port=8990, listen_host="127.0.0.1"),
        repair_url="")
    expect = (
        'srtsrc uri="srt://127.0.0.1:8990?mode=listener&latency=300" ! tsdemux name=d d. '
        "! h264parse ! avdec_h264 max-threads=4 thread-type=frame "
        "! queue leaky=downstream max-size-time=400000000 max-size-buffers=0 max-size-bytes=0 "
        "! videorate ! videoscale add-borders=true ! videoconvert n-threads=2 "
        "! video/x-raw,format=v210,width=1920,height=1080,framerate=30/1,"
        "pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt709 "
        "! mxlsink name=sink domain=/mxl-domain flow-id=9e111e00-aaaa-4bbb-8ccc-000000000001 "
        'label="Guest 1" description="contributor SRT-direct-listen video (guest1 :8990)" '
        'group-hint="Guest1:Video" sync=false'
    )
    assert core.pipe._launch == expect


def test_srt_listen_audio_launch_is_pinned():
    from adapters import SrtListenerGuestAudioAdapter
    core = cc.ContributionCore(
        SrtListenerGuestAudioAdapter(path="guest1", flow_id="a1111e00-aaaa-4bbb-8ccc-000000000001",
                                     label="Guest 1 Audio", listen_port=9090, listen_host="127.0.0.1"),
        repair_url="")
    expect = (
        'srtsrc uri="srt://127.0.0.1:9090?mode=listener&latency=300" ! tsdemux name=d d. '
        "! queue ! aacparse ! avdec_aac ! audioconvert ! audioresample "
        "! audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2,channel-mask=(bitmask)0x3 "
        "! queue max-size-buffers=32 "
        "! mxlsink name=sink domain=/mxl-domain flow-id=a1111e00-aaaa-4bbb-8ccc-000000000001 "
        'label="Guest 1 Audio" description="contributor SRT-direct-listen audio (guest1 :9090)" '
        'group-hint="Guest1Audio:Audio" sync=false'
    )
    assert core.pipe._launch == expect
