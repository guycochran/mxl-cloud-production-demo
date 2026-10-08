#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Load the facility manifest (config/facility.json) — the single source of
truth for domain, network, control ports, and flow UUIDs.

Before this, every tool hardcoded its own copy of the UUID/port/label map and
they drifted. Import from here instead:

    from facility import FACILITY, video_flows, audio_flows, flow_uuid

    FLOWS = video_flows()                 # {name: uuid}  (drop-in for old dicts)
    dst   = flow_uuid('audio', 'pgm')     # one UUID by role
    dom   = FACILITY['facility']['domain_path']

Resolution order for the manifest file:
  1. $MXL_FACILITY_JSON if set (useful when a tool is docker-cp'd to /tmp)
  2. config/facility.json next to the repo (walks up from this file)
  3. ./facility.json in the cwd (last resort for bring-up copies)

Fails loud with a clear message if none is found — a missing manifest should
stop a tool at startup, not surface later as a wrong/empty UUID.
"""
import json
import os

_SEARCH_NAMES = ("config/facility.json", "facility.json")


def _candidates():
    env = os.environ.get("MXL_FACILITY_JSON")
    if env:
        yield env
    here = os.path.dirname(os.path.abspath(__file__))
    # walk up from tools/ looking for config/facility.json (repo checkout)
    d = here
    for _ in range(6):
        for name in _SEARCH_NAMES:
            yield os.path.join(d, name)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # cwd fallbacks (bring-up copies tools + config side by side into /tmp)
    for name in _SEARCH_NAMES:
        yield os.path.join(os.getcwd(), name)


# Env overrides for the manifest's network section. The manifest values remain the
# defaults, so nothing changes unless a variable is set (see docs/CONFIG.md).
_NETWORK_ENV = {
    "MXL_VM_IP": "mxl_vm",
    "MXL_VM_INTERNAL_IP": "mxl_vm_internal",
    "MXL_DOCKER_GATEWAY": "docker_gateway",
}


def _apply_env_overrides(data):
    net = data.get("network")
    if isinstance(net, dict):
        for env_name, key in _NETWORK_ENV.items():
            v = os.environ.get(env_name, "").strip()
            if v:
                net[key] = v
    return data


def _load():
    tried = []
    for path in _candidates():
        tried.append(path)
        if path and os.path.isfile(path):
            with open(path) as fh:
                data = json.load(fh)
            data["_path"] = path
            return _apply_env_overrides(data)
    raise FileNotFoundError(
        "facility manifest not found. Set $MXL_FACILITY_JSON or place "
        "config/facility.json in the repo. Looked in:\n  " + "\n  ".join(tried)
    )


FACILITY = _load()


def _flow_map(section):
    """{name: uuid} for a *_flows section, skipping _comment/_note keys."""
    return {
        name: entry["uuid"]
        for name, entry in FACILITY[section].items()
        if isinstance(entry, dict) and "uuid" in entry
    }


def video_flows():
    """{name: uuid} for all video flows (drop-in for old hardcoded FLOWS)."""
    return _flow_map("video_flows")


def audio_flows():
    """{name: uuid} for all audio flows."""
    return _flow_map("audio_flows")


def flow_uuid(kind, name):
    """One UUID by role. kind in {'video','audio'}."""
    section = "video_flows" if kind == "video" else "audio_flows"
    return FACILITY[section][name]["uuid"]


def flow_label(kind, name):
    section = "video_flows" if kind == "video" else "audio_flows"
    return FACILITY[section][name].get("label", name)


def control_port(name):
    """Control-API port by process name (e.g. 'selector' -> 9604)."""
    return FACILITY["control_api"]["ports"][name]["port"]


def domain_path():
    return FACILITY["facility"]["domain_path"]


if __name__ == "__main__":
    # `python3 facility.py` — quick sanity dump
    print(f"manifest: {FACILITY['_path']}")
    print(f"domain:   {domain_path()}")
    print(f"mxl_vm:   {FACILITY['network']['mxl_vm']}")
    print("video flows:")
    for n, u in video_flows().items():
        print(f"  {n:10s} {u}")
    print("audio flows:")
    for n, u in audio_flows().items():
        print(f"  {n:10s} {u}")
