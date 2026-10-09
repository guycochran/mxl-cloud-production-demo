#!/usr/bin/env sh
# ingress-soak.sh — objective PASS/FAIL soak verdict for a contribution ingest,
# in the MXL Timing Model's OWN terms.
#
# The Timing Model (dmf-mxl docs, "Timing Model") says a healthy writer keeps flow
# latency "low and mostly constant" over hours, with no runaway drift. `mxl-info`
# exposes no transfer latency directly (see docs/GCP-BUILD-PLAN.md), so we read the
# IN-005 ingress registry that contribution_core writes (MXL_INGRESS_DIR, default
# /tmp/mxl-ingress/<flow8>.json) — it carries the restamp's running wall-clock error
# (err_ms = grid residual), the locked offset, the grain step, and the dropped-frame count
# (the grid servo's burst-guard activity). We sample those over a window and apply:
#
#   PASS  when, across the window:
#     - the record keeps UPDATING (mono_ns advances) AND frames advance (essence moving)
#     - |err_ms| stays within ERR_BAND_MS (bounded — "low"), over real numeric samples only
#       (lock/relock records carry err_ms=null and are skipped, not counted as 0)
#     - err_ms does not trend away: last-vs-first spread < ERR_DRIFT_MS ("constant")
#     - dropped does not climb faster than DROP_RATE_MAX per committed frame (no burst-guard
#       thrash — the grid never settling is the Oct-7 60fps regression signal)
#   STALE when the record never updated across the window (can't judge — run longer).
#   FAIL otherwise, naming which clause broke.
#
# This is the explicit pass/fail for the 60fps restamp HW re-verify and any guest soak.
#
# Usage:
#   tools/ingress-soak.sh [flow8|path]   # default: every *.json in MXL_INGRESS_DIR
#   DURATION=300 INTERVAL=10 tools/ingress-soak.sh guest1   # 5-min soak, sample /10s
#
# Pure POSIX sh + the JSON records. No jq (parse like scripts/doctor.sh's jget).

set -eu

INGRESS_DIR="${MXL_INGRESS_DIR:-/tmp/mxl-ingress}"
DURATION="${DURATION:-120}"          # soak window, seconds
INTERVAL="${INTERVAL:-10}"           # sample period, seconds
ERR_BAND_MS="${ERR_BAND_MS:-250}"    # |err| must stay under this (bounded = "low")
ERR_DRIFT_MS="${ERR_DRIFT_MS:-150}"  # max first..last |err| spread (no runaway = "constant")
# Max dropped-frames climb PER committed frame before it's thrash. A 60->30 decimation legit
# drops ~1 per committed frame, so allow 3 (headroom); sustained higher = the burst guard
# churning (the grid never settling), which is the real 60fps regression signal.
DROP_RATE_MAX="${DROP_RATE_MAX:-3}"

# jget <json> <key> — scalar value for "key" (same approach as scripts/doctor.sh).
jget() { sed -n "s/.*\"$2\" *: *\"\{0,1\}\([^,\"}]*\).*/\1/p" <<EOF | head -1
$1
EOF
}

# resolve the record file(s) to watch
records() {
  if [ "$#" -ge 1 ] && [ -n "${1:-}" ]; then
    case "$1" in
      */*|*.json) echo "$1" ;;              # explicit path
      *) echo "$INGRESS_DIR/$1.json" ;;      # a flow8 shorthand
    esac
  else
    # all records in the dir
    for f in "$INGRESS_DIR"/*.json; do [ -e "$f" ] && echo "$f"; done
  fi
}

soak_one() {
  rec="$1"
  name="$(basename "$rec" .json)"
  if [ ! -e "$rec" ]; then
    echo "FAIL  $name  — no ingress record at $rec (ingest not running, or MXL_INGRESS_DIR unset?)"
    return 1
  fi
  first_err=""; last_err=""; max_abs=0; first_frames=""; last_frames=""
  first_dropped=""; last_dropped=""; samples=0; err_samples=0
  first_mono=""; last_mono=""
  elapsed=0
  # is_int X -> true if X is a plain (optionally -) integer, not 'null'/''/decimal-only
  is_int() { case "${1#-}" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac }
  while [ "$elapsed" -le "$DURATION" ]; do
    body="$(cat "$rec" 2>/dev/null || true)"
    err="$(jget "$body" err_ms)"
    frames="$(jget "$body" frames)";       frames="${frames:-0}"
    dropped="$(jget "$body" dropped)";     dropped="${dropped:-0}"
    mono="$(jget "$body" mono_ns)";        mono="${mono:-0}"
    # err_ms is JSON null on lock/relock records — do NOT treat null as 0 (that masks drift).
    # Only fold a real numeric err into the band/drift stats.
    err_i="${err%.*}"
    if is_int "$err_i"; then
      abs="${err_i#-}"
      [ "$abs" -gt "$max_abs" ] 2>/dev/null && max_abs="$abs"
      [ -z "$first_err" ] && first_err="$err_i"
      last_err="$err_i"; err_samples=$((err_samples + 1))
      err_disp="${err_i}ms"
    else
      err_disp="(null)"
    fi
    is_int "$frames" || frames=0
    [ -z "$first_frames" ] && { first_frames="$frames"; first_dropped="$dropped"; first_mono="$mono"; }
    last_frames="$frames"; last_dropped="$dropped"; last_mono="$mono"
    samples=$((samples + 1))
    printf '  %-10s t=%3ds  err=%7s  frames=%-7s dropped=%s\n' "$name" "$elapsed" "$err_disp" "$frames" "$dropped"
    [ "$elapsed" -ge "$DURATION" ] && break
    sleep "$INTERVAL"; elapsed=$((elapsed + INTERVAL))
  done

  # ---- verdict ----
  verdict=PASS; why=""
  # 0. the record must actually be UPDATING over the window (mono_ns advances). If it never
  #    changed, the ingest isn't writing records fast enough to judge — report STALE, not a
  #    false FAIL/PASS. (Run longer, or lower the ingest's diag_every.)
  if [ "$samples" -ge 2 ] && [ "$last_mono" = "$first_mono" ]; then
    echo "STALE $name — ingress record did not update across the ${DURATION}s window (mono_ns unchanged); can't judge. Increase DURATION or lower the ingest diag_every."
    return 2
  fi
  # 1. essence moving: frames must advance (only meaningful once the record is updating)
  if [ "$last_frames" -le "$first_frames" ] 2>/dev/null; then
    verdict=FAIL; why="$why frames-not-advancing(${first_frames}->${last_frames}=DRIFT);"
  fi
  # 2/3. err bounded + constant — only if we actually saw numeric err samples (lock-only
  #      windows carry null err and are not a pass signal on their own).
  if [ "$err_samples" -ge 1 ]; then
    if [ "$max_abs" -gt "$ERR_BAND_MS" ] 2>/dev/null; then
      verdict=FAIL; why="$why err-out-of-band(|${max_abs}|>${ERR_BAND_MS}ms);"
    fi
    spread=$((last_err - first_err)); spread="${spread#-}"
    if [ "$spread" -gt "$ERR_DRIFT_MS" ] 2>/dev/null; then
      verdict=FAIL; why="$why err-drifting(${first_err}->${last_err}=${spread}ms>${ERR_DRIFT_MS});"
    fi
  else
    # Every sample carried err_ms=null (lock/relock-only window): we never observed a real
    # grid residual, so we CANNOT assert the flow is healthy. Report STALE, not PASS — a
    # lock-only window is not a pass signal on its own.
    echo "STALE $name — no numeric err_ms samples across the window (lock-only records); can't judge grid residual. Run longer so a 'diag' record is sampled."
    return 2
  fi
  # 4. burst guard not thrashing: dropped must not climb faster than DROP_RATE_MAX per frame.
  #    Steady 60->30 decimation drops ~half the frames (dropped grows ~1 per committed frame) —
  #    that's EXPECTED. Thrash = dropped climbing while frames barely advance.
  d_gain=$((last_dropped - first_dropped)); f_gain=$((last_frames - first_frames))
  if [ "$f_gain" -gt 0 ] && [ "$d_gain" -gt $(( f_gain * DROP_RATE_MAX )) ] 2>/dev/null; then
    verdict=FAIL; why="$why drop-thrash(dropped+${d_gain} vs frames+${f_gain});"
  fi
  [ "$verdict" = PASS ] && why=" |err|<=${max_abs}ms bounded+constant (${err_samples} err samples), frames ${first_frames}->${last_frames}, dropped +$((last_dropped - first_dropped))"
  echo "$verdict  $name —$why"
  [ "$verdict" = PASS ]
}

echo "ingress-soak: ${DURATION}s window, every ${INTERVAL}s  (band=±${ERR_BAND_MS}ms, drift<${ERR_DRIFT_MS}ms)"
rc=0
for rec in $(records "$@"); do
  soak_one "$rec" || rc=1
done
[ "$rc" -eq 0 ] && echo "SOAK PASS" || echo "SOAK FAIL"
exit "$rc"
