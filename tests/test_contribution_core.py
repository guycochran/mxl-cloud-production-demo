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


def test_resync_requires_sustained_drift():
    # A momentary hiccup must NOT yank the offset — re-sync only after N consecutive.
    assert cc.RESYNC_COUNT >= 2
    assert cc.RESYNC_NS > cc.MARGIN_NS


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
