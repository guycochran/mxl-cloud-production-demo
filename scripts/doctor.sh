#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# doctor.sh — one-glance health check for an MXL switcher lab.
#
# Answers the question every MXL experimenter eventually asks: "is it actually
# working, and if not, WHERE is it broken?" It checks the three layers an adopter
# cares about, top to bottom:
#
#   1. CONTAINERS  — are the media-function control APIs up? (:9600/:9602/:9604/:9605/:9601)
#   2. FLOWS       — what video flows are in the domain, and are they carrying FRESH
#                    media? Not just "a grain ticked" — UNIQUE frames per second, the
#                    only signal that catches a repeat-wedged reader (FINDINGS §: a
#                    stale reader delivers 30 buffers/s of the SAME frame; rate looks
#                    perfect, picture is frozen). This is the operational form of the
#                    IN-001 "started != content-liveness" finding.
#   3. PROGRAM     — is the selector attached, is the keyer up, is WebRTC serving?
#
# Liveness (ufps) is read from the grain probe's /mxl-domain/thumbs/grains.json if the
# probe is running; without it, doctor still reports presence/fps-less status and tells
# you how to turn liveness on. Backend-free, stdlib curl/docker only — runs on the bare
# host of a quickstart box.
#
# Usage:  scripts/doctor.sh            # one-shot report, exits 0 if program is healthy
#         scripts/doctor.sh --watch    # refresh every 3s
#         SELECTOR_CONTAINER=... MXL_DOMAIN=... scripts/doctor.sh   # override defaults
set -u

DOMAIN="${MXL_DOMAIN:-/mxl-domain}"
SEL_CTR="${SELECTOR_CONTAINER:-input-selector}"
GRAINS_JSON="${GRAINS_JSON:-}"           # if empty, auto-read from inside $SEL_CTR
UFPS_MIN="${UFPS_MIN:-20}"               # below this a "live" flow is suspect (judder/wedge)
WATCH=0
[ "${1:-}" = "--watch" ] && WATCH=1

# ── tty colours (degrade to plain when piped) ──────────────────────────────────
if [ -t 1 ]; then G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else G=''; Y=''; R=''; D=''; N=''; fi
ok(){   printf '%s  %-22s %s%s%s\n' "$G" "$1" "$G" "${2:-OK}" "$N"; }
warn(){ printf '%s  %-22s %s%s%s\n' "$Y" "$1" "$Y" "$2" "$N"; }
bad(){  printf '%s  %-22s %s%s%s\n' "$R" "$1" "$R" "$2" "$N"; }

api_up(){ curl -s -m 3 -o /dev/null "http://127.0.0.1:$1/pipeline/status"; }
api_json(){ curl -s -m 3 "http://127.0.0.1:$1/pipeline/status" 2>/dev/null; }

# jq-free single-field pull from a flat JSON object
jget(){ sed -n "s/.*\"$2\" *: *\"\{0,1\}\([^,\"}]*\).*/\1/p" <<<"$1" | head -1; }

# flow label -> uuid, via mxl-info inside the selector container (same method the
# watcher + quickstart use).
declare -A FLOW_UUID
load_flows(){
  FLOW_UUID=()
  local out
  out=$(docker exec "$SEL_CTR" sh -c \
        "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null) || return 1
  # lines like:  "   Video : <uuid> - <label>"
  while IFS= read -r line; do
    case "$line" in
      *" : "*" - "*)
        local label uuid
        label="${line##* - }"
        uuid="${line% - *}"; uuid="${uuid##* : }"
        uuid="$(echo "$uuid" | tr -d '[:space:]')"
        label="$(echo "$label" | sed 's/^ *//;s/ *$//')"
        [ "${#uuid}" -eq 36 ] && FLOW_UUID["$label"]="$uuid"
        ;;
    esac
  done <<<"$out"
}

# liveness map: name -> ufps, from the grain probe JSON (if present)
declare -A LIVE_UFPS
load_liveness(){
  LIVE_UFPS=()
  local raw=""
  if [ -n "$GRAINS_JSON" ] && [ -f "$GRAINS_JSON" ]; then
    raw=$(cat "$GRAINS_JSON" 2>/dev/null)
  else
    raw=$(docker exec "$SEL_CTR" sh -c "cat $DOMAIN/thumbs/grains.json" 2>/dev/null)
  fi
  [ -z "$raw" ] && return 1
  # {"cam": {"bps":..,"ufps":29.4}, "guest1": null, ...}  — pull name+ufps pairs
  while IFS=$'\t' read -r name ufps; do
    [ -n "$name" ] && LIVE_UFPS["$name"]="$ufps"
  done < <(echo "$raw" | sed 's/},/}\n/g' \
            | sed -n 's/.*"\([a-zA-Z0-9_]*\)" *: *{[^}]*"ufps" *: *\([0-9.]*\).*/\1\t\2/p')
  return 0
}

report(){
  local healthy=1
  printf '\n%sMXL DOCTOR%s  domain=%s  selector=%s\n' "$D" "$N" "$DOMAIN" "$SEL_CTR"
  printf '%s─────────────────────────────────────────────%s\n' "$D" "$N"

  # 1. containers / control APIs
  local -A PORTS=( [test-generator]=9600 [file-player]=9602 [input-selector]=9604 \
                   [html5-keyer]=9605 [mxl2webrtc]=9601 )
  for name in test-generator file-player input-selector html5-keyer mxl2webrtc; do
    if api_up "${PORTS[$name]}"; then ok "$name" "OK :${PORTS[$name]}"
    else bad "$name" "DOWN :${PORTS[$name]}"; healthy=0; fi
  done

  # 2. flows + liveness
  printf '%s  — flows —%s\n' "$D" "$N"
  local have_probe=1
  load_liveness || have_probe=0
  # Explicit flow-label -> grain_probe key map (tools/grain_probe.py FLOWS). An
  # approximate heuristic silently mislabels liveness, so map the known labels
  # exactly; anything unknown reports presence only (ufps n/a), never a wrong number.
  declare -A PROBE_KEY=( ["Pattern Video"]=pattern ["Clip Video"]=playout \
    ["Guest 1"]=guest1 ["Guest 2"]=guest2 ["CAM Live"]=cam ["CAM 2 Live"]=cam2 \
    ["Selector PGM"]=selector ["Keyer PGM"]=keyer ["Layout"]=layout )
  if load_flows && [ "${#FLOW_UUID[@]}" -gt 0 ]; then
    for label in "${!FLOW_UUID[@]}"; do
      local key="${PROBE_KEY[$label]:-}"
      local ufps=""; [ -n "$key" ] && ufps="${LIVE_UFPS[$key]:-}"
      if [ "$have_probe" = 0 ]; then
        warn "$label" "present (liveness unknown — probe not running)"
      elif [ -z "$ufps" ]; then
        warn "$label" "present · ufps n/a"
      elif awk "BEGIN{exit !($ufps < $UFPS_MIN)}"; then
        bad "$label" "LIVE but STALE ${ufps} ufps (<${UFPS_MIN} — frozen/judder?)"; healthy=0
      else
        ok "$label" "LIVE ${ufps} ufps"
      fi
    done
  else
    bad "flows" "mxl-info returned nothing (is $SEL_CTR running?)"; healthy=0
  fi
  [ "$have_probe" = 0 ] && printf '%s    (start tools/grain_probe.py for true unique-fps liveness)%s\n' "$D" "$N"

  # 3. program path
  printf '%s  — program —%s\n' "$D" "$N"
  local sel; sel=$(api_json 9604)
  if [ -n "$sel" ]; then
    local ai; ai=$(jget "$sel" active_input)
    [ -n "$ai" ] && ok "selector" "attached · active slot $ai" || warn "selector" "up, no active input"
  else bad "selector" "no status"; healthy=0; fi
  local keyer; keyer=$(api_json 9605)
  if [ -n "$keyer" ]; then
    local kon; kon=$(jget "$keyer" key_on)
    ok "keyer" "up · key_on=${kon:-?}"
  else warn "keyer" "no status (graphics may be off)"; fi
  if api_up 9601; then ok "webrtc encoder" "control :9601 up"; else bad "webrtc encoder" "DOWN"; healthy=0; fi
  # The encoder control API answering does NOT prove the viewer can watch: mediamtx
  # must be up and serving the WHEP/signaling page on :8889. Check that too so
  # PROGRAM HEALTHY actually means watchable.
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx mediamtx; then
    ok "mediamtx" "container up"
  else bad "mediamtx" "not running"; healthy=0; fi
  if curl -s -m 3 -o /dev/null "http://127.0.0.1:8889/"; then
    ok "viewer/signaling" ":8889 serving"
  else warn "viewer/signaling" ":8889 no response (viewers can't watch)"; fi

  printf '%s─────────────────────────────────────────────%s\n' "$D" "$N"
  if [ "$healthy" = 1 ]; then printf '%sPROGRAM HEALTHY%s\n\n' "$G" "$N"; return 0
  else printf '%sATTENTION NEEDED%s — see red rows above\n\n' "$R" "$N"; return 1; fi
}

if [ "$WATCH" = 1 ]; then
  while true; do clear; report; sleep 3; done
else
  report
fi
