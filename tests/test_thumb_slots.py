"""mxl_thumbs.py discovers its multiview slots from the live domain (reviewer R2:
no hardcoded, facility-specific UUIDs). These tests pin the discovery contract
against a synthetic domain of flow_def.json files — no GStreamer, no threads.

discover_slots() is imported from the probe module; the run loop is guarded under
__main__, and Gst.init happens there, so importing for the test starts nothing.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"

pytest.importorskip("gi")  # the probe imports GStreamer bindings at module top


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, str(TOOLS))
    try:
        m = importlib.import_module("mxl_thumbs")
    finally:
        sys.path.pop(0)
    return m


def _flow(domain, uuid, fmt, grouphint=None, label=""):
    d = domain / f"{uuid}.mxl-flow"
    d.mkdir()
    body = {"id": uuid, "format": f"urn:x-nmos:format:{fmt}", "label": label}
    if grouphint:
        body["tags"] = {"urn:x-nmos:tag:grouphint/v1.0": [grouphint]}
    (d / "flow_def.json").write_text(json.dumps(body))


def test_discovers_video_sources_by_role(mod, tmp_path):
    _flow(tmp_path, "d3e15194-6d1f-5955-8d52-52e7076b7a99", "video", "Pattern:Video", "Pattern Video")
    _flow(tmp_path, "9998da48-9041-5ae1-b730-d0a918563107", "video", "Playout:Video", "Clip Video")
    _flow(tmp_path, "9e111e00-aaaa-4bbb-8ccc-000000000001", "video", "Guest1:Video", "Guest 1")
    slots = mod.discover_slots(str(tmp_path))
    assert slots == {
        "pattern": "d3e15194-6d1f-5955-8d52-52e7076b7a99",
        "playout": "9998da48-9041-5ae1-b730-d0a918563107",
        "guest1": "9e111e00-aaaa-4bbb-8ccc-000000000001",
    }


def test_audio_flows_are_skipped(mod, tmp_path):
    _flow(tmp_path, "d3e15194-6d1f-5955-8d52-52e7076b7a99", "video", "Pattern:Video")
    _flow(tmp_path, "7c1dd9ba-ca2c-5737-889b-abdd23c06020", "audio", "Pattern:Audio")
    slots = mod.discover_slots(str(tmp_path))
    assert list(slots) == ["pattern"]  # the audio flow produced no tile


def test_program_outputs_excluded(mod, tmp_path):
    """selector/keyer are the program bus, not selectable source tiles."""
    _flow(tmp_path, "5a617f19-7c48-598c-9e94-4611c6316273", "video", "Selector:Video", "Selector PGM")
    _flow(tmp_path, "86efffa4-9b4e-558c-8816-7e200e20cf17", "video", "Keyer:Video", "Keyer PGM")
    _flow(tmp_path, "9e333e00-aaaa-4bbb-8ccc-000000000001", "video", "Guest3:Video", "Guest 3")
    slots = mod.discover_slots(str(tmp_path))
    assert list(slots) == ["guest3"]


def test_stable_collapses_to_friendly_guest_name(mod, tmp_path):
    """A never-interrupt slot's GuestNStable flow shows under the friendly 'guestN'."""
    _flow(tmp_path, "57ab5e00-aaaa-4bbb-8ccc-000000000001", "video", "Guest5Stable:Video", "Guest 5 Stable")
    slots = mod.discover_slots(str(tmp_path))
    assert slots == {"guest5": "57ab5e00-aaaa-4bbb-8ccc-000000000001"}


def test_env_force_overrides_discovery(mod, tmp_path):
    forced = "cam=aaa,guest1=bbb"
    slots = mod.discover_slots(str(tmp_path), forced=forced)
    assert slots == {"cam": "aaa", "guest1": "bbb"}


def test_missing_domain_is_empty_not_error(mod, tmp_path):
    assert mod.discover_slots(str(tmp_path / "does-not-exist")) == {}


def test_untagged_flow_falls_back_to_uuid_prefix(mod, tmp_path):
    _flow(tmp_path, "abcd1234-6d1f-5955-8d52-52e7076b7a99", "video")  # no grouphint
    slots = mod.discover_slots(str(tmp_path))
    assert slots == {"abcd1234": "abcd1234-6d1f-5955-8d52-52e7076b7a99"}
