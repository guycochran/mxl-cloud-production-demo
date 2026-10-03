"""Participant pairs a guest's video+audio legs (v0.3 Phase 3).

The whole point of Participant is that it composes the TWO per-essence legs from the
manifest WITHOUT importing the media stack — a supervisor on a bare box must be able
to build the launch commands. So these tests import participant directly (no stubbed
gi needed for the data path) and pin: flows match the manifest, and legs() emits argv
byte-compatible with the existing guest_ingest.py / guest_audio.py CLI contracts.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import participant as P  # noqa: E402  (pure data path, no gi import at module load)

MANIFEST = json.loads((REPO / "config" / "facility.json").read_text())


def test_flows_resolve_from_manifest():
    for n in (1, 2):
        p = P.Participant.guest(n)
        assert p.video_flow() == MANIFEST["video_flows"][f"guest{n}"]["uuid"]
        assert p.audio_flow() == MANIFEST["audio_flows"][f"guest{n}"]["uuid"]


def test_legs_are_two_distinct_essences():
    legs = P.Participant.guest(1).legs()
    assert [leg["essence"] for leg in legs] == ["video", "audio"]
    # distinct tools + distinct flows → distinct processes (never multiplexed)
    assert legs[0]["tool"] == "guest_ingest.py"
    assert legs[1]["tool"] == "guest_audio.py"
    assert legs[0]["argv"][1] != legs[1]["argv"][1]


def test_leg_argv_matches_existing_cli_contract():
    """bring-up/quickstart call `guest_ingest.py <path> <flow> <label> [jitter]` and
    `guest_audio.py <path> <flow> <label> [jitter]`. Participant must emit exactly
    that positional shape so a supervisor can adopt it with no behaviour change."""
    p = P.Participant.guest(2, label="Guest 2")
    v, a = p.legs()
    assert v["argv"][:3] == ["guest2", MANIFEST["video_flows"]["guest2"]["uuid"], "Guest 2"]
    assert a["argv"][:3] == ["guest2", MANIFEST["audio_flows"]["guest2"]["uuid"], "Guest 2 Audio"]
    # jitter present as the 4th positional, numeric-string
    assert v["argv"][3].isdigit() and a["argv"][3].isdigit()


def test_builds_without_gstreamer():
    """The data path (legs/flows) must not require gi — only video_adapter()/
    audio_adapter() do. Importing participant + calling legs() already proves this
    (adapters import is lazy); assert the lazy boundary explicitly."""
    src = (TOOLS / "participant.py").read_text()
    # the top-level import block must NOT import adapters (that pulls gi)
    head = src.split("class Participant")[0]
    assert "from adapters import" not in head, "participant.py imports adapters at module load (pulls gi)"


def test_default_transport_is_srt_direct():
    """The hardware-proven SRT-direct path must be the DEFAULT (review #1): an adopter
    cloning master should get it, not the flap-prone RTSP path. Guard against regressing
    the default back to rtsp in the guest entrypoints + Participant."""
    import os
    # entrypoints: default branch must reference the SRT-direct adapters
    gi = (TOOLS / "guest_ingest.py").read_text()
    ga = (TOOLS / "guest_audio.py").read_text()
    assert "MXL_GUEST_TRANSPORT" in gi and "MXL_GUEST_TRANSPORT" in ga
    assert "SrtGuestVideoAdapter" in gi, "guest_ingest default is not SRT-direct"
    assert "SrtGuestAudioAdapter" in ga, "guest_audio default is not SRT-direct"

    # Participant: default transport selects the SRT-direct adapter classes
    os.environ.pop("MXL_GUEST_TRANSPORT", None)
    import importlib, participant as pmod
    importlib.reload(pmod)
    p = pmod.Participant.guest(1)
    assert p.transport == "srt-direct"
    # rtsp escape hatch still available
    p2 = pmod.Participant.guest(1, transport="rtsp")
    assert p2.transport == "rtsp"
