"""R7 self-healer tests. The healer (tools/mxl-selfheal.sh) is a bash script that
polls local control ports and recovers the two drift failures we hit all session
(selector down, relay waiting-for-audio). We test its DECISION logic without a real
facility by stubbing `curl` and `docker` on PATH to return canned pipeline states,
then asserting the healer acts only when something is broken.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HEALER = REPO / "tools" / "mxl-selfheal.sh"


def _run_with_stubs(tmp_path, selector_status, relay_status):
    """Run one healer pass with stubbed curl+docker. Returns (stdout, actions[]).
    actions are the pipeline/start calls the healer issued (recorded by the stubs)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    actions_log = tmp_path / "actions.log"

    # curl stub: GET :9604/pipeline/status -> selector_status; POSTs get logged.
    curl = bindir / "curl"
    curl.write_text(f"""#!/usr/bin/env bash
args="$*"
if [[ "$args" == *"9604/pipeline/status"* && "$args" != *"-X POST"* ]]; then
  echo '{json.dumps(selector_status)}'; exit 0
fi
if [[ "$args" == *"-X POST"* ]]; then
  echo "POST $args" >> "{actions_log}"; echo '{{}}'; exit 0
fi
echo '{{}}'
""")
    # docker stub: `docker exec <ctr> sh -c "curl ... 9600/pipeline/status"` -> relay_status;
    # `docker exec ... mxl-info ...` -> a flow listing with a Keyer PGM line.
    docker = bindir / "docker"
    docker.write_text(f"""#!/usr/bin/env bash
args="$*"
if [[ "$args" == *"9600/pipeline/status"* ]]; then
  echo '{json.dumps(relay_status)}'; exit 0
fi
if [[ "$args" == *"mxl-info"* ]]; then
  printf '\\tVideo : dd44ee55-0000-4000-8000-000000000001 - Keyer PGM\\n'
  printf '\\tVideo : aa11bb22-0000-4000-8000-000000000001 - Pattern Video\\n'
  printf '\\tVideo : bb22cc33-0000-4000-8000-000000000001 - Clip Video\\n'
  exit 0
fi
if [[ "$args" == *"-X POST"* || "$args" == *"pipeline/start"* ]]; then
  echo "DOCKER-POST $args" >> "{actions_log}"; exit 0
fi
exit 0
""")
    for f in (curl, docker):
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    # use the repo manifest so selector_inputs_json has inputs to restore
    env["MXL_FACILITY_JSON"] = str(REPO / "config" / "facility.json")
    r = subprocess.run(["bash", str(HEALER)], capture_output=True, text=True, env=env)
    actions = actions_log.read_text().splitlines() if actions_log.exists() else []
    return r.stdout, actions


def test_healer_exists_and_parses():
    assert HEALER.is_file()
    assert subprocess.run(["bash", "-n", str(HEALER)]).returncode == 0


def test_healthy_facility_is_left_alone(tmp_path):
    out, actions = _run_with_stubs(
        tmp_path,
        selector_status={"running": "True", "active_input": 0, "input_flow_uuids": ["x"]},
        relay_status={"running": "True", "mode": "video"},
    )
    assert actions == [], f"healer acted on a healthy facility: {actions}"


def test_selector_down_is_restarted(tmp_path):
    out, actions = _run_with_stubs(
        tmp_path,
        selector_status={"running": "False", "input_flow_uuids": []},
        relay_status={"running": "True", "mode": "video"},
    )
    # it should POST a selector /pipeline/start with the manifest's inputs
    assert any("9604/pipeline/start" in a for a in actions), f"selector not restarted: {actions}"
    assert "SELECTOR DOWN" in out


def test_relay_waiting_for_audio_is_restarted_video_only(tmp_path):
    out, actions = _run_with_stubs(
        tmp_path,
        selector_status={"running": "True", "active_input": 0, "input_flow_uuids": ["x"]},
        relay_status={"running": "False", "mode": "video+audio"},  # the wedge
    )
    # it should restart the relay in video-only mode via docker exec
    assert any("pipeline/start" in a and "DOCKER-POST" in a for a in actions), f"relay not restarted: {actions}"
    assert "RELAY" in out


def test_both_broken_both_healed(tmp_path):
    out, actions = _run_with_stubs(
        tmp_path,
        selector_status={"running": "False", "input_flow_uuids": []},
        relay_status={"running": "False", "mode": "video+audio"},
    )
    assert any("9604/pipeline/start" in a for a in actions)
    assert any("DOCKER-POST" in a and "pipeline/start" in a for a in actions)
