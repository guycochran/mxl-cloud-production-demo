#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""desired_state_report — report-only desired vs actual drift table (v0.1 sketch).

Compares config/facility.json (desired) to a mocked actual-state JSON and prints
a multi-plane table:

    FUNCTION / DESIRED / PROCESS / FLOW / CADENCE / UNIQUE_FPS / UPSTREAM /
    DOWNSTREAM / DRIFT

Three planes from day one — process ≠ media ≠ dependency. A process that is
`running` with unique_fps=0 is DRIFT (alive process / dead media).

No docker, no heal, no process signals, no live facility touch. Repair is
intentionally not implemented here (see docs/DESIRED-STATE-v0.md).

Usage:
  python3 tools/desired_state_report.py \
    --facility config/facility.json \
    --actual tests/fixtures/actual_state_mock.json

  python3 tools/desired_state_report.py ... --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Default media floors when facility.json has no desired_state block yet.
_DEFAULT_CADENCE = 30
_DEFAULT_UNIQUE_MIN = 1


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"expected object in {path}")
    return data


def _desired_functions(facility: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a minimal desired map from the manifest.

    Prefers facility['desired_state']['functions'] when present; otherwise
    derives from control_api.ports (skipping parked) plus a synthetic
    pgm_audio row from audio_flows when available.
    """
    ds = facility.get("desired_state")
    if isinstance(ds, dict) and isinstance(ds.get("functions"), dict):
        out: dict[str, dict[str, Any]] = {}
        for name, spec in ds["functions"].items():
            if name.startswith("_") or not isinstance(spec, dict):
                continue
            out[name] = _desired_from_spec(facility, name, spec)
        return out

    ports = (
        facility.get("control_api", {}).get("ports", {})
        if isinstance(facility.get("control_api"), dict)
        else {}
    )
    # Lightweight dependency hints from program path (generic, not site-specific).
    dep_hints = {
        "selector": {"upstream": ["cam", "playout"], "downstream": ["keyer"]},
        "keyer": {"upstream": ["selector"], "downstream": ["encoder"]},
        "encoder": {"upstream": ["keyer", "pgm_audio"], "downstream": ["egress"]},
        "cam_ingest": {"upstream": ["camera"], "downstream": ["selector"]},
        "file_player": {"upstream": ["clip"], "downstream": ["selector"]},
        "test_generator": {"upstream": ["pattern"], "downstream": ["selector"]},
        "pgm_audio": {"upstream": ["guests", "voice"], "downstream": ["encoder"]},
    }
    out = {}
    for name, spec in ports.items():
        if name.startswith("_") or not isinstance(spec, dict):
            continue
        if spec.get("parked"):
            continue
        port = spec.get("port", "?")
        role = spec.get("role", spec.get("process", name))
        deps = dep_hints.get(name, {"upstream": ["—"], "downstream": ["—"]})
        out[name] = {
            "summary": f"running · port {port} · {role} · ≥{_DEFAULT_UNIQUE_MIN} ufps",
            "process": "running",
            "flow": "present",
            "cadence_fps": _DEFAULT_CADENCE,
            "unique_fps_min": _DEFAULT_UNIQUE_MIN,
            "upstream": deps["upstream"],
            "downstream": deps["downstream"],
            "fail_policy": "hold_last",
        }

    audio = facility.get("audio_flows", {})
    if isinstance(audio, dict) and isinstance(audio.get("pgm"), dict):
        deps = dep_hints["pgm_audio"]
        out.setdefault(
            "pgm_audio",
            {
                "summary": (
                    f"running · program audio · bypass ok · ≥{_DEFAULT_UNIQUE_MIN} ufps"
                ),
                "process": "running",
                "flow": "present",
                "cadence_fps": _DEFAULT_CADENCE,
                "unique_fps_min": _DEFAULT_UNIQUE_MIN,
                "upstream": deps["upstream"],
                "downstream": deps["downstream"],
                "fail_policy": "audio_bypass",
            },
        )
    return out


def _desired_from_spec(
    facility: dict[str, Any], name: str, spec: dict[str, Any]
) -> dict[str, Any]:
    ports = facility.get("control_api", {}).get("ports", {})
    port_key = spec.get("control_port_key", name)
    port = "?"
    if isinstance(ports, dict) and isinstance(ports.get(port_key), dict):
        port = ports[port_key].get("port", "?")
    media = spec.get("media") if isinstance(spec.get("media"), dict) else {}
    deps = spec.get("deps") if isinstance(spec.get("deps"), dict) else {}
    policy = spec.get("fail_policy", "hold_last")
    u_min = int(media.get("unique_fps_min", _DEFAULT_UNIQUE_MIN))
    cadence = int(media.get("cadence_fps", _DEFAULT_CADENCE))
    role = spec.get("role", name)
    return {
        "summary": (
            f"running · port {port} · {role} · ≥{u_min} ufps · fail_policy={policy}"
        ),
        "process": spec.get("process", "running"),
        "flow": media.get("flow", "present"),
        "cadence_fps": cadence,
        "unique_fps_min": u_min,
        "upstream": list(deps.get("upstream") or ["—"]),
        "downstream": list(deps.get("downstream") or ["—"]),
        "fail_policy": policy,
    }


def _actual_functions(actual: dict[str, Any]) -> dict[str, dict[str, Any]]:
    funcs = actual.get("functions", actual)
    if not isinstance(funcs, dict):
        raise SystemExit("actual-state JSON needs a 'functions' object")
    out: dict[str, dict[str, Any]] = {}
    for name, spec in funcs.items():
        if name.startswith("_"):
            continue
        if not isinstance(spec, dict):
            out[name] = {
                "process": "unknown",
                "flow": "unknown",
                "cadence": None,
                "unique_fps": None,
                "upstream": "—",
                "downstream": "—",
                "bypass": None,
            }
            continue
        media = spec.get("media") if isinstance(spec.get("media"), dict) else {}
        deps = spec.get("deps") if isinstance(spec.get("deps"), dict) else {}
        process = str(spec.get("process", media.get("process", "unknown")))
        flow = str(spec.get("flow", media.get("flow", "unknown")))
        cadence = spec.get("cadence", media.get("cadence"))
        unique_fps = spec.get("unique_fps", media.get("unique_fps"))
        upstream = spec.get("upstream", deps.get("upstream", "—"))
        downstream = spec.get("downstream", deps.get("downstream", "—"))
        if isinstance(upstream, list):
            upstream = ",".join(upstream) if upstream else "—"
        if isinstance(downstream, list):
            downstream = ",".join(downstream) if downstream else "—"
        out[name] = {
            "process": process,
            "flow": flow,
            "cadence": cadence,
            "unique_fps": unique_fps,
            "upstream": str(upstream),
            "downstream": str(downstream),
            "bypass": spec.get("bypass"),
        }
    return out


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    return str(value)


def _planes_ok(desired: dict[str, Any], actual: dict[str, Any]) -> bool:
    """HEALTHY only when process, media, and (light) dependency checks pass."""
    if actual["process"] != desired.get("process", "running"):
        if not (
            desired.get("fail_policy") == "audio_bypass"
            and actual.get("bypass") == "active"
            and actual["process"] == "running"
        ):
            return False

    if actual["flow"] != desired.get("flow", "present"):
        if not (
            desired.get("fail_policy") == "audio_bypass"
            and actual.get("bypass") == "active"
        ):
            return False

    u_min = int(desired.get("unique_fps_min", _DEFAULT_UNIQUE_MIN))
    unique = actual.get("unique_fps")
    if unique is None or int(unique) < u_min:
        if not (
            desired.get("fail_policy") == "audio_bypass"
            and actual.get("bypass") == "active"
        ):
            return False

    for side in ("upstream", "downstream"):
        val = str(actual.get(side, "")).lower()
        if "broken" in val or val == "missing":
            return False

    return True


def compute_drift(
    desired: dict[str, dict[str, Any]],
    actual: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare desired vs actual across process/media/dependency planes."""
    names = sorted(set(desired) | set(actual))
    rows = []
    for name in names:
        d = desired.get(name)
        a = actual.get(name)
        if d is None:
            rows.append(
                {
                    "function": name,
                    "desired": "(not in desired)",
                    "process": a["process"] if a else "—",
                    "flow": a["flow"] if a else "—",
                    "cadence": _fmt_num(a["cadence"] if a else None),
                    "unique_fps": _fmt_num(a["unique_fps"] if a else None),
                    "upstream": a["upstream"] if a else "—",
                    "downstream": a["downstream"] if a else "—",
                    "drift": True,
                }
            )
            continue
        if a is None:
            rows.append(
                {
                    "function": name,
                    "desired": d["summary"],
                    "process": "missing",
                    "flow": "absent",
                    "cadence": "—",
                    "unique_fps": "—",
                    "upstream": "—",
                    "downstream": "—",
                    "drift": True,
                }
            )
            continue
        rows.append(
            {
                "function": name,
                "desired": d["summary"],
                "process": a["process"],
                "flow": a["flow"],
                "cadence": _fmt_num(a["cadence"]),
                "unique_fps": _fmt_num(a["unique_fps"]),
                "upstream": a["upstream"],
                "downstream": a["downstream"],
                "drift": not _planes_ok(d, a),
            }
        )
    return rows


def format_table(rows: list[dict[str, Any]]) -> str:
    headers = (
        "FUNCTION",
        "DESIRED",
        "PROCESS",
        "FLOW",
        "CADENCE",
        "UNIQUE_FPS",
        "UPSTREAM",
        "DOWNSTREAM",
        "DRIFT",
    )
    cells = [
        (
            r["function"],
            r["desired"],
            r["process"],
            r["flow"],
            r["cadence"],
            r["unique_fps"],
            r["upstream"],
            r["downstream"],
            "DRIFT" if r["drift"] else "ok",
        )
        for r in rows
    ]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [
        fmt(headers),
        fmt(tuple("-" * w for w in widths)),
        *[fmt(c) for c in cells],
    ]
    return "\n".join(lines) + "\n"


def build_report(facility_path: Path, actual_path: Path) -> list[dict[str, Any]]:
    facility = _load_json(facility_path)
    actual = _load_json(actual_path)
    desired = _desired_functions(facility)
    actual_fns = _actual_functions(actual)
    return compute_drift(desired, actual_fns)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Report-only multi-plane desired vs actual drift (no heal)."
    )
    p.add_argument(
        "--facility",
        type=Path,
        default=Path("config/facility.json"),
        help="path to facility.json (desired state)",
    )
    p.add_argument(
        "--actual",
        type=Path,
        required=True,
        help="path to mocked actual-state JSON",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON rows instead of the table",
    )
    args = p.parse_args(argv)

    if not args.facility.is_file():
        print(
            f"desired_state_report: facility not found: {args.facility}",
            file=sys.stderr,
        )
        return 2
    if not args.actual.is_file():
        print(
            f"desired_state_report: actual not found: {args.actual}",
            file=sys.stderr,
        )
        return 2

    rows = build_report(args.facility, args.actual)
    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_table(rows))

    return 1 if any(r["drift"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
