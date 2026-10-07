#!/usr/bin/env sh
# ingress-soak.sh — objective PASS/FAIL soak verdict for a contribution ingest,
# in the MXL Timing Model's OWN terms.
#
# The Timing Model (dmf-mxl docs, "Timing Model") says a healthy writer keeps flow
# latency "low and mostly constant" over hours, with no runaway drift. `mxl-info`
# exposes no transfer latency directly (see docs/GCP-BUILD-PLAN.md), so we read the
# IN-005 ingress registry that contribution_core writes (MXL_INGRESS_DIR, default
# /tmp/mxl-ingress/<flow8>.json) — it carries the restamp's running wall-clock error
# (err_ms), the locked offset, the grain step, and the hard-relock count. We sample
# those over a window and apply the criterion:
#
#   PASS  when, across the window:
#     - the record keeps UPDATING (frames advancing — essence is moving, not DRIFT)
#     - |err_ms| stays within ERR_BAND_MS (bounded — "low")
#     - err_ms does not trend away: last-vs-first spread < ERR_DRIFT_MS ("constant")
#     - hard_relocks does not increase (no re-lock churn — the Oct-7 60fps symptom)
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
  first_relocks=""; last_relocks=""; samples=0
  elapsed=0
  while [ "$elapsed" -le "$DURATION" ]; do
    body="$(cat "$rec" 2>/dev/null || true)"
    err="$(jget "$body" err_ms)";        err="${err:-0}"
    frames="$(jget "$body" frames)";     frames="${frames:-0}"
    relocks="$(jget "$body" hard_relocks)"; relocks="${relocks:-0}"
    # integer math in ms (strip any decimal)
    err_i="${err%.*}"; [ "$err_i" = "-" ] && err_i=0; err_i="${err_i:-0}"
    abs="${err_i#-}"
    [ "$abs" -gt "$max_abs" ] 2>/dev/null && max_abs="$abs"
    if [ -z "$first_err" ]; then first_err="$err_i"; first_frames="$frames"; first_relocks="$relocks"; fi
    last_err="$err_i"; last_frames="$frames"; last_relocks="$relocks"
    samples=$((samples + 1))
    printf '  %-10s t=%3ds  err=%6sms  frames=%-7s relocks=%s\n' "$name" "$elapsed" "$err_i" "$frames" "$relocks"
    [ "$elapsed" -ge "$DURATION" ] && break
    sleep "$INTERVAL"; elapsed=$((elapsed + INTERVAL))
  done

  # ---- verdict ----
  verdict=PASS; why=""
  # 1. essence moving: frames must advance
  if [ "$last_frames" -le "$first_frames" ] 2>/dev/null; then
    verdict=FAIL; why="$why frames-not-advancing(${first_frames}->${last_frames}=DRIFT);"
  fi
  # 2. bounded: |err| within band
  if [ "$max_abs" -gt "$ERR_BAND_MS" ] 2>/dev/null; then
    verdict=FAIL; why="$why err-out-of-band(|${max_abs}|>${ERR_BAND_MS}ms);"
  fi
  # 3. constant: first..last spread small
  spread=$((last_err - first_err)); spread="${spread#-}"
  if [ "$spread" -gt "$ERR_DRIFT_MS" ] 2>/dev/null; then
    verdict=FAIL; why="$why err-drifting(${first_err}->${last_err}=${spread}ms>${ERR_DRIFT_MS});"
  fi
  # 4. no re-lock churn: hard_relocks must not climb
  if [ "$last_relocks" -gt "$first_relocks" ] 2>/dev/null; then
    verdict=FAIL; why="$why relock-churn(${first_relocks}->${last_relocks});"
  fi
  [ "$verdict" = PASS ] && why=" |err|<=${max_abs}ms bounded+constant, ${samples} samples, no relock churn"
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
