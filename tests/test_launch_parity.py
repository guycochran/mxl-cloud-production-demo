"""Golden-string parity test for the guest SRT launch pipeline.

During the seam refactor we verified (via a stubbed-gi dry run) that the guest launch
string was BYTE-IDENTICAL to the pre-refactor cam/guest ingest. This formalizes that
check: the full gst-launch string for a guest slot is pinned here. If a refactor
changes it, this fails loudly and the diff shows exactly what moved — then you decide
whether the change is intentional (update the golden) or a regression (revert).

The string is built through the REAL ContributionCore + SrtGuestAdapter (stubbed gi),
so it reflects the actual shipped pipeline, not a hand-written copy.
"""
import contribution_core as cc
from adapters import SrtGuestAdapter

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
