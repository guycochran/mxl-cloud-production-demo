"""Guard the hard-won ContributionCore behaviour against "cleanup" regressions.

These tests lock down the invariants that cost real debugging to discover (FINDINGS
§1/§6, the seam refactor, the ZoomISO dry-run). They run with a stubbed `gi` (see
conftest.py) so they need no GStreamer — they assert on the LAUNCH STRING the core
builds and on the pure branching logic, which is exactly where a well-meaning refactor
would silently break the media path.

If one of these fails, do NOT "fix the test" — re-read the cited finding first.
"""
import contribution_core as cc
from adapters import (
    CaptionFileAdapter,
    MakitoAdapter,
    RtspCamAdapter,
    SrtGuestAdapter,
    ZoomIsoMxlAdapter,
    zoomiso_adapters,
)


# ── cadence constants: the single most load-bearing numbers in the repo ──────────
def test_margin_is_exactly_two_grains():
    # 2 grains @30fps. Bigger margins starve readers ("too late" wedges). FINDINGS §1.
    assert cc.MARGIN_NS == 66_000_000
    one_grain_ns = 1_000_000_000 // 30
    assert abs(cc.MARGIN_NS - 2 * one_grain_ns) < 1_000_000  # ~2 grains, rounding slack


def test_slew_servo_constants_are_sane():
    # The offset is driven by a PROPORTIONAL slew, not a step re-sync (HW Oct-7: a step
    # churned every batch on a 60fps-decimated-to-30 source). Guard the servo shape:
    assert 0 < cc.SLEW_GAIN < 1            # a fraction of the error per frame, not a jump
    assert 0 < cc.SLEW_MAX_NS < cc.FRAME_NS  # per-frame correction capped well below one grain
    assert cc.HARD_RELOCK_NS >= cc.FRAME_NS  # only a GROSS error triggers a one-shot hard re-lock


def test_canon_caps_are_v210_1080p30_progressive():
    for token in ("format=v210", "width=1920", "height=1080",
                  "framerate=30/1", "progressive"):
        assert token in cc.CANON_CAPS


# ── _build_launch: the conform/native branch + the double-videorate bug ──────────
def _core(adapter):
    # repair_url="" disables the announce; diag irrelevant. Construction builds the
    # launch string via the stubbed parse_launch — that's what we assert on.
    return cc.ContributionCore(adapter, repair_url="")


def test_conform_path_has_exactly_one_videorate():
    """The seam refactor once introduced a DOUBLE videorate (one in the adapter front
    end, one in the core conform). videorate belongs ONCE, in the core. Regression
    guard: a decode adapter's launch string must contain exactly one `videorate`."""
    core = _core(SrtGuestAdapter(path="guest1", flow_id="f" * 36, label="Guest 1"))
    launch = core.pipe._launch
    assert launch.count("videorate") == 1, launch


def test_conform_path_ends_in_canonical_v210_sink():
    core = _core(RtspCamAdapter(url="rtsp://cam/stream", flow_id="a" * 36))
    launch = core.pipe._launch
    assert "format=v210" in launch
    assert "mxlsink" in launch
    # conform stages present exactly once each
    assert launch.count("videoscale") == 1
    assert launch.count("videoconvert") == 1


def test_native_path_skips_conform_entirely():
    """needs_conform=False => NO videorate/videoscale/videoconvert/v210 conform.
    The native source already emits canonical grains; re-conforming would be wrong."""
    adapter = ZoomIsoMxlAdapter(source_flow_id="s" * 36, flow_id="d" * 36)
    assert adapter.needs_conform is False
    core = _core(adapter)
    launch = core.pipe._launch
    assert "videorate" not in launch
    assert "videoconvert" not in launch
    assert "mxlsrc" in launch
    assert "mxlsink" in launch


def test_mxlsrc_uses_video_flow_id_not_flow_id():
    """mxlSRC uses `video-flow-id`; mxlSINK uses `flow-id`. Mixing them silently
    reads/writes nothing. Verified via gst-inspect in the dry-run; guard it."""
    adapter = ZoomIsoMxlAdapter(source_flow_id="s" * 36, flow_id="d" * 36)
    frag = adapter.source_fragment()
    assert "video-flow-id=" + "s" * 36 in frag
    # the source fragment must NOT set a bare flow-id= (that's the sink's property)
    assert "flow-id=" not in frag.replace("video-flow-id=", "")


# ── orthogonal properties: "native" != "don't touch timestamps" ──────────────────
def test_is_native_mxl_is_derived_from_needs_conform():
    a = SrtGuestAdapter(path="g1", flow_id="f" * 36, label="Guest 1")
    assert a.needs_conform is True
    assert a.is_native_mxl is False

    z = ZoomIsoMxlAdapter(source_flow_id="s" * 36, flow_id="d" * 36)
    assert z.needs_conform is False
    assert z.is_native_mxl is True


def test_timing_policy_default_restamp_for_decode_sources():
    # Remote/foreign-clock decode sources must restamp onto the local clock.
    assert SrtGuestAdapter(path="g", flow_id="f" * 36, label="G").timing_policy == "restamp"
    assert RtspCamAdapter(url="rtsp://c", flow_id="a" * 36).timing_policy == "restamp"


def test_zoomiso_timing_policy_is_provisional_align_but_overridable():
    # 'align' is a PROVISIONAL default: native MXL does not imply "on my clock".
    assert ZoomIsoMxlAdapter(source_flow_id="s" * 36, flow_id="d" * 36).timing_policy == "align"
    # one kwarg flips it at beta — no rewrite.
    flipped = ZoomIsoMxlAdapter(source_flow_id="s" * 36, flow_id="d" * 36,
                                timing_policy="restamp")
    assert flipped.timing_policy == "restamp"


# ── zoomiso_adapters factory: both flow shapes handled, chosen at beta ────────────
def test_zoomiso_per_participant_maps_n_to_n():
    srcs = [f"{i:036d}" for i in range(3)]
    slots = [chr(97 + i) * 36 for i in range(3)]
    adapters = zoomiso_adapters(srcs, slots, mode="per_participant")
    assert len(adapters) == 3
    assert [a.flow_id for a in adapters] == slots
    assert [a.source_flow_id for a in adapters] == srcs
    assert [a.label for a in adapters] == ["Zoom 1", "Zoom 2", "Zoom 3"]


def test_zoomiso_per_participant_clamps_to_min_length():
    adapters = zoomiso_adapters(["s" * 36, "t" * 36], ["x" * 36], mode="per_participant")
    assert len(adapters) == 1  # min(2 sources, 1 slot)


def test_zoomiso_composite_collapses_to_one_slot():
    adapters = zoomiso_adapters(["s" * 36, "t" * 36], ["x" * 36, "y" * 36],
                                mode="composite")
    assert len(adapters) == 1
    assert adapters[0].flow_id == "x" * 36
    assert adapters[0].source_flow_id == "s" * 36


def test_zoomiso_unknown_mode_raises():
    import pytest
    with pytest.raises(ValueError):
        zoomiso_adapters(["s" * 36], ["x" * 36], mode="galaxy-brain")


# ── repair_url resolution: quickstart tier runs backend-free ─────────────────────
def test_repair_url_disabled_by_empty_string():
    core = cc.ContributionCore(
        SrtGuestAdapter(path="g1", flow_id="f" * 36, label="Guest 1"),
        repair_url="",
    )
    assert core.repair_url is None  # no prodbots announce in the quickstart tier


def test_repair_url_none_literal_also_disables():
    core = cc.ContributionCore(
        SrtGuestAdapter(path="g1", flow_id="f" * 36, label="Guest 1"),
        repair_url="none",
    )
    assert core.repair_url is None


def test_repair_url_defaults_to_no_announce(monkeypatch):
    """BOUNDARY: with nothing configured the open core must NOT call any backend.
    A clone that instantiates ContributionCore must never phone home by default."""
    monkeypatch.delenv("MXL_REPAIR_URL", raising=False)
    core = cc.ContributionCore(SrtGuestAdapter(path="g1", flow_id="f" * 36, label="Guest 1"))
    assert core.repair_url is None


def test_repair_url_env_opt_in(monkeypatch):
    """A deployment opts IN explicitly via the env var (what the OHG bring-up does)."""
    monkeypatch.setenv("MXL_REPAIR_URL", "https://example.test/api/mxl/repair")
    core = cc.ContributionCore(SrtGuestAdapter(path="g1", flow_id="f" * 36, label="Guest 1"))
    assert core.repair_url == "https://example.test/api/mxl/repair"


# ── R1: persistent-flow mode (fold create-once + swap-reader into the ingest) ────
# The wedge (review R1): the supervisor restarts the whole ingest on a source
# reconnect, which RECREATES the mxlsink flow and wedges the selector's cold reader.
# Persistent mode keeps the flow alive and rebuilds ONLY the source leg. These lock
# down the pure logic; the HW proof (stable flow inode/ctime across 10 reconnects,
# continuous cadence, program-follows-cut) is an integration job on AVX hardware.
import gi as _gi  # noqa: E402  (the stub; just to reach Gst.last_bins)
_Gst = _gi.repository.Gst


def _persist_adapter():
    a = SrtGuestAdapter(path="g1", flow_id="f" * 36, label="Guest 1")
    return a


def test_persistent_off_by_default():
    """Default must preserve the HW-proven supervisor-restart path — opt-in only."""
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    assert core.persistent is False


def test_persistent_env_opt_in(monkeypatch):
    monkeypatch.setenv("MXL_INGEST_PERSISTENT", "1")
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    assert core.persistent is True


def test_persistent_env_can_force_off(monkeypatch):
    """Env override wins over an adapter that defaults persistent_flow=True."""
    a = _persist_adapter()
    a.persistent_flow = True
    monkeypatch.setenv("MXL_INGEST_PERSISTENT", "0")
    core = cc.ContributionCore(a, repair_url="none")
    assert core.persistent is False


def test_persistent_adapter_property_opt_in(monkeypatch):
    monkeypatch.delenv("MXL_INGEST_PERSISTENT", raising=False)
    a = _persist_adapter()
    a.persistent_flow = True
    core = cc.ContributionCore(a, repair_url="none")
    assert core.persistent is True


def test_persistent_tail_has_same_conform_and_sink_as_oneshot(monkeypatch):
    """The split pipeline's TAIL must carry the identical conform + mxlsink the
    one-shot launch uses — otherwise the two modes diverge on the media path."""
    monkeypatch.setenv("MXL_INGEST_PERSISTENT", "1")
    _Gst.last_bins = []
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    tail = core._tail_fragment()
    # conform + sink identical to the one-shot path
    assert "videorate" in tail
    assert cc.CANON_CAPS in tail
    assert "mxlsink name=sink" in tail
    assert f"flow-id={'f' * 36}" in tail
    # the jitter queue the source leg links into, created once
    assert "queue name=jbuf" in tail
    # exactly one videorate (the double-videorate bug must not reappear in the tail)
    assert tail.count("videorate") == 1


def test_persistent_init_builds_tail_then_source_leg(monkeypatch):
    """__init__ in persistent mode parses the tail bin first, then the source leg."""
    monkeypatch.setenv("MXL_INGEST_PERSISTENT", "1")
    _Gst.last_bins = []
    cc.ContributionCore(_persist_adapter(), repair_url="none")
    assert len(_Gst.last_bins) == 2
    assert "mxlsink name=sink" in _Gst.last_bins[0]   # tail
    # source leg = the adapter's own front end (SrtGuestAdapter uses rtspsrc), and it
    # must NOT carry the conform/sink — those live only in the persistent tail.
    assert "rtspsrc" in _Gst.last_bins[1]
    assert "mxlsink" not in _Gst.last_bins[1]


def test_monotonic_relock_never_rewinds_the_flow(monkeypatch):
    """The core of the R1 fix: on a leg rebuild the new source PTS restarts near 0,
    so the restamp must RE-LOCK the offset forward of the last grain already written —
    never rewind the flow (a rewind is exactly the selector's 'grain too early' read)."""
    monkeypatch.setenv("MXL_INGEST_PERSISTENT", "1")
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    s = core.state
    # simulate: we already wrote up to last_mapped, then a rebuild cleared the lock
    s["last_mapped"] = 10_000_000_000      # 10s of flow already written
    s["offset"] = None
    s["relock"] = True
    # the new leg's first frame arrives with a small PTS (fresh session) and a clock
    # time that would map it BACKWARD under a naive now-based lock
    new_leg_pts = 0
    now = 500_000_000                      # clock only 0.5s in (well before last_mapped)
    # replicate the clamp the probe applies on re-lock
    offset = now - new_leg_pts + cc.MARGIN_NS
    min_pts = s["last_mapped"] + cc.FRAME_NS
    if new_leg_pts + offset < min_pts:
        offset = min_pts - new_leg_pts
    mapped = new_leg_pts + offset
    assert mapped >= s["last_mapped"] + cc.FRAME_NS   # strictly forward — no rewind


# ── _grain_ns: the monotonic step must be RATE-DERIVED, not a 30fps constant ──────
# Oct 7 2026 fix: a 60fps source's floor was using FRAME_NS (33.3ms), a step 2x too
# large, which surfaced as the Oct-6 `err=-41s / fps=173`. The step must come from the
# pad's NEGOTIATED framerate. These use duck-typed caps/pad — no real GStreamer needed.
class _FakeStructure:
    def __init__(self, num, den):
        self._fr = (num, den)

    def get_fraction(self, name):
        assert name == "framerate"
        if self._fr is None:
            return (False, 0, 0)
        return (True, self._fr[0], self._fr[1])


class _FakeCaps:
    def __init__(self, num=None, den=1):
        self._st = _FakeStructure(num, den) if num is not None else None

    def get_size(self):
        return 1 if self._st is not None else 0

    def get_structure(self, i):
        return self._st

    def to_string(self):
        if self._st is None:
            return "video/x-raw"
        n, d = self._st._fr
        return f"video/x-raw, format=(string)v210, framerate=(fraction){n}/{d}"


class _FakePad:
    def __init__(self, caps):
        self._caps = caps

    def get_current_caps(self):
        return self._caps


def test_grain_ns_is_rate_derived():
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    # 30fps (the canonical conform rate) == FRAME_NS
    assert core._grain_ns(_FakePad(_FakeCaps(30, 1))) == cc.FRAME_NS
    # 60fps => half the step (the exact bug: floor was 2x too large)
    assert core._grain_ns(_FakePad(_FakeCaps(60, 1))) == round(1_000_000_000 / 60)
    # 59.94 (60000/1001) => correct fractional grain
    assert core._grain_ns(_FakePad(_FakeCaps(60000, 1001))) == round(1001 * 1_000_000_000 / 60000)
    # 50fps (EU) => 20ms
    assert core._grain_ns(_FakePad(_FakeCaps(50, 1))) == 20_000_000


def test_grain_ns_falls_back_to_frame_ns_without_framerate():
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    assert core._grain_ns(_FakePad(_FakeCaps(None))) == cc.FRAME_NS     # empty caps
    assert core._grain_ns(_FakePad(None)) == cc.FRAME_NS                # no caps yet


# ── IN-005 ingress registry: the restamp must leave a TRACEABLE per-flow record ───
def test_ingress_record_captures_provenance_and_timing(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("MXL_INGRESS_DIR", str(tmp_path))
    monkeypatch.setenv("MXL_GUEST_TRANSPORT", "srt-listen")
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    core.state["offset"] = 66_000_000
    core.state["n"] = 1200
    core._write_ingress_record(_FakePad(_FakeCaps(30, 1)), event="diag",
                               err_ns=-4_700_000, grain_ns=cc.FRAME_NS)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    # provenance (IN-005: identify the signal + its source timing)
    assert rec["transport"] == "srt-listen"
    assert rec["source_caps"] and "framerate=(fraction)30/1" in rec["source_caps"]
    assert rec["essence"] == "video"
    # timing adjustments (IN-005: traceable offsets)
    assert rec["offset_ms"] == 66.0
    assert rec["err_ms"] == -4.7
    assert rec["grain_ns"] == cc.FRAME_NS
    assert rec["event"] == "diag" and rec["frames"] == 1200


def test_ingress_record_disabled_by_empty_dir(tmp_path, monkeypatch):
    # An adopter can opt OUT (no file written) by clearing MXL_INGRESS_DIR.
    monkeypatch.setenv("MXL_INGRESS_DIR", "")
    core = cc.ContributionCore(_persist_adapter(), repair_url="none")
    assert core._ingress_path is None
    core._write_ingress_record(_FakePad(_FakeCaps(30, 1)), event="locked")  # must not raise
    assert list(tmp_path.glob("*.json")) == []


# ── Data essence: ANC / closed-caption (video/smpte291) data flow (Tier 3.1) ──────
def test_caption_adapter_is_data_essence_and_preserve():
    a = CaptionFileAdapter(srt_file="show.srt", flow_id="d" * 36, label="Captions")
    assert a.essence == "data"
    assert a.timing_policy == "preserve"          # ST-2038 grains are frame-aligned; no restamp
    assert a.group_hint == "Captions:Data"        # group_hint knows the data essence


def test_caption_adapter_source_fragment_is_the_st2038_chain():
    a = CaptionFileAdapter(srt_file="show.srt", flow_id="d" * 36)
    frag = a.source_fragment()
    # mirrors the gst-mxl-rs v1.1.0 README producer chain
    for tok in ("filesrc location=show.srt", "subparse", "tttocea608",
                "ccconverter", "closedcaption/x-cea-608", "cctost2038anc",
                "meta/x-st-2038"):
        assert tok in frag, frag
    assert "30000/1001" in frag                   # README default / NTSC 608 framerate


def test_data_launch_is_st2038_to_mxlsink_no_video_conform():
    a = CaptionFileAdapter(srt_file="show.srt", flow_id="d" * 36, label="CC")
    launch = _core(a).pipe._launch
    assert "meta/x-st-2038,alignment=frame" in launch   # canonical data caps enforced
    assert "mxlsink" in launch
    # a data flow must NOT get the video conform transforms
    assert "videorate" not in launch
    assert "videoconvert" not in launch
    assert "format=v210" not in launch
    assert "audioconvert" not in launch
