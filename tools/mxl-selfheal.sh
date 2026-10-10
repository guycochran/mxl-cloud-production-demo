#!/usr/bin/env bash
# mxl-selfheal.sh — a portable self-healer for the quickstart MXL switcher.
#
# Opt-in. quickstart.sh does NOT start this by default. Set MXL_SELFHEAL=1 when
# launching quickstart (or run this script yourself) to enable the watcher.
#
# The bare adopter stack has no watchdog. Across a long session two failures can
# black out the program with no automatic recovery:
#
#   1. SELECTOR DOWN — the input-selector pipeline stops (running:false, inputs:[]),
#      so every cut returns 409 "not attached" and the program freezes.
#   2. RELAY WAITING FOR AUDIO — mxl2webrtc defaults to mode:video+audio and waits
#      forever for an audio_pgm flow that a bare quickstart never starts, so the
#      WebRTC program monitor shows "stream not found" even though video exists.
#
# This watcher polls the LOCAL control ports (no external backend) and fixes
# exactly those two, idempotently. Safe to run as a plain loop, a systemd unit, or
# a cron. It only ever acts when something is actually broken.
#
#   tools/mxl-selfheal.sh            # one check+heal pass, then exit
#   tools/mxl-selfheal.sh --watch    # loop every MXL_SELFHEAL_INTERVAL (default 15s)
#   sudo MXL_SELFHEAL=1 scripts/quickstart.sh   # start watcher with the lab
#
# Config (env):
#   MXL_FACILITY_JSON   manifest with the selector inputs to restore (the one
#                       quickstart generated); else falls back to re-discovery.
#   MXL_SELFHEAL_INTERVAL   seconds between --watch passes (default 15)
#   SELECTOR_CONTAINER      default input-selector
#   RELAY_CONTAINER         default mxl2webrtc
set -u

INTERVAL="${MXL_SELFHEAL_INTERVAL:-15}"
SEL_CTR="${SELECTOR_CONTAINER:-input-selector}"
RELAY_CTR="${RELAY_CONTAINER:-mxl2webrtc}"
DOMAIN="${MXL_DOMAIN:-/mxl-domain}"
KEYER_FLOW=""   # discovered lazily

# Use `docker` directly if we can (quickstart runs as root), else `sudo docker`.
if docker ps >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi

log(){ printf '%s mxl-selfheal: %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
_get(){ curl -s -m 5 "http://127.0.0.1:$1$2" 2>/dev/null; }
_post(){ curl -s -m 25 -X POST -H 'Content-Type: application/json' -d "$3" "http://127.0.0.1:$1$2" 2>/dev/null; }
_json(){ python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1',''))" 2>/dev/null; }

# UUIDs that actually exist in the domain right now (one per line).
existing_flows(){
  $DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
    | grep -oE '[0-9a-f-]{36}' | sort -u
}

# selector inputs to restore: the manifest's inputs INTERSECTED with the flows that
# actually exist. Restarting with a flow that isn't present makes the selector start
# FAIL (found on HW — the manifest lists 4, but a fresh/partial facility may have
# only 2), so we must never wire a phantom flow. Falls back to the two base sources
# by label if no manifest is given.
selector_inputs_json(){
  local present; present=$(existing_flows)
  [ -n "$present" ] || return 1   # can't read the domain — don't guess
  if [ -n "${MXL_FACILITY_JSON:-}" ] && [ -f "$MXL_FACILITY_JSON" ]; then
    MXL_PRESENT="$present" python3 - "$MXL_FACILITY_JSON" <<'PY' 2>/dev/null && return 0
import json, os, sys
d = json.load(open(sys.argv[1]))
vf = d.get("video_flows", {})
roles = d.get("program", {}).get("selector_inputs") or d.get("program", {}).get("layout_inputs") or []
present = set(os.environ.get("MXL_PRESENT", "").split())
uuids = [vf[r]["uuid"] for r in roles if r in vf and vf[r]["uuid"] in present]
# only emit if we found at least one real, present source
if uuids:
    print(json.dumps(uuids))
else:
    sys.exit(1)
PY
  fi
  # fallback: discover pattern + clip by label, keep only the present ones
  local pat clip out=()
  pat=$($DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
    | grep -E "^\s+Video : [0-9a-f-]{36} - Pattern Video\$" | grep -oE '[0-9a-f-]{36}' | head -1)
  clip=$($DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
    | grep -E "^\s+Video : [0-9a-f-]{36} - Clip Video\$" | grep -oE '[0-9a-f-]{36}' | head -1)
  for u in "$pat" "$clip"; do
    [ -n "$u" ] && printf '%s\n' "$present" | grep -qx "$u" && out+=("$u")
  done
  [ ${#out[@]} -gt 0 ] && printf '[%s]' "$(printf '"%s",' "${out[@]}" | sed 's/,$//')"
}

keyer_out_flow(){
  [ -n "$KEYER_FLOW" ] && { printf '%s' "$KEYER_FLOW"; return; }
  KEYER_FLOW=$($DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
    | grep -E "^\s+Video : [0-9a-f-]{36} - Keyer PGM\$" | grep -oE '[0-9a-f-]{36}' | head -1)
  printf '%s' "$KEYER_FLOW"
}

# ── heal 1: selector down → restart with its inputs + re-cut to slot 0 ──────────
heal_selector(){
  local st running
  st=$(_get 9604 /pipeline/status) || return 0
  running=$(printf '%s' "$st" | _json running)
  [ "$running" = "True" ] && return 0   # healthy
  local inputs; inputs=$(selector_inputs_json)
  [ -n "$inputs" ] || { log "selector down but no inputs to restore (manifest + discovery empty) — skipping"; return 0; }
  log "SELECTOR DOWN (running=$running) — restarting with inputs $inputs"
  _post 9604 /pipeline/start "{\"domain_path\":\"$DOMAIN\",\"input_flow_uuids\":$inputs,\"grouphint\":\"Input-Selector\",\"description\":\"program out\",\"label\":\"Selector PGM\"}" >/dev/null
  sleep 2
  _post 9604 /pipeline/active-input '{"slot":0}' >/dev/null
  log "selector restarted"
}

# ── heal 2: relay waiting for audio → (re)start it video-only ───────────────────
heal_relay(){
  local st running mode
  st=$($DOCKER exec "$RELAY_CTR" sh -c "curl -s -m5 http://127.0.0.1:9600/pipeline/status" 2>/dev/null) || return 0
  running=$(printf '%s' "$st" | _json running)
  mode=$(printf '%s' "$st" | _json mode)
  # broken state = not running, OR running in a mode that waits for an absent audio flow
  if [ "$running" = "True" ] && [ "$mode" = "video" ]; then return 0; fi
  local key; key=$(keyer_out_flow)
  [ -n "$key" ] || { log "relay unhealthy but keyer flow not found yet — skipping"; return 0; }
  log "RELAY unhealthy (running=$running mode=$mode) — restarting VIDEO-ONLY on keyer $key"
  $DOCKER exec "$RELAY_CTR" sh -c "curl -s -m5 -X POST http://127.0.0.1:9600/pipeline/stop >/dev/null; sleep 1; \
    curl -s -m8 -X POST -H 'Content-Type: application/json' \
    -d '{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$key\",\"audio_flow_uuid\":null,\"mode\":\"video\"}' \
    http://127.0.0.1:9600/pipeline/start >/dev/null" 2>/dev/null
  log "relay restarted video-only"
}

# ── heal 3: PGM publish dropped → republish encoder (the grey-screen fix) ───────
# The recurring grey screen: the selector output keeps ADVANCING (source + cut fine) but
# mxl2webrtc's publish to mediamtx silently dies downstream (ICE/renegotiation/mediamtx
# hiccup) — the relay's /pipeline/status still says running:true, so heal_relay above
# doesn't catch it. The only reliable signal is mediamtx itself: 0 program bytes while
# the selector output is moving = publish dropped. Fix = restart the encoder read of the
# current program flow (same body pgm-heal uses). We require the selector to be ADVANCING
# first so we never "heal" a genuinely-stopped program into a busy-loop.
SEL_OUT_FLOW=""
sel_out_flow(){
  [ -n "$SEL_OUT_FLOW" ] && { printf '%s' "$SEL_OUT_FLOW"; return; }
  SEL_OUT_FLOW=$(_get 9604 /pipeline/status | _json output_flow_uuid)
  printf '%s' "$SEL_OUT_FLOW"
}
headidx(){ $DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -f $1 2>/dev/null" 2>/dev/null | grep -i 'Head index' | grep -oE '[0-9]+' | head -1; }
mtx_pgm_bytes(){ _get 9997 /v3/paths/list | python3 -c "import sys,json;print(sum(p.get('bytesReceived',0) for p in json.load(sys.stdin).get('items',[])))" 2>/dev/null; }
heal_publish(){
  local out; out=$(sel_out_flow)
  [ -n "$out" ] || return 0
  # is the program source actually advancing? (don't republish a legitimately-idle PGM)
  local a b; a=$(headidx "$out"); sleep 1; b=$(headidx "$out")
  [ -n "$a" ] && [ -n "$b" ] && [ "$b" -gt "$a" ] 2>/dev/null || return 0   # not advancing — nothing to publish
  # selector is moving. is mediamtx actually receiving program bytes?
  local p0 p1; p0=$(mtx_pgm_bytes); sleep 2; p1=$(mtx_pgm_bytes)
  [ -n "$p0" ] && [ -n "$p1" ] || return 0            # can't read mediamtx — leave alone
  [ "$p1" -gt "$p0" ] 2>/dev/null && return 0          # publishing fine
  # ADVANCING but NO bytes to mediamtx = the grey screen. Republish the encoder.
  local src key; key=$(keyer_out_flow)
  if [ -n "$key" ]; then local ka kb; ka=$(headidx "$key"); sleep 1; kb=$(headidx "$key")
    [ -n "$ka" ] && [ -n "$kb" ] && [ "$kb" -gt "$ka" ] 2>/dev/null && src="$key"; fi
  [ -n "$src" ] || src="$out"                          # keyer not advancing -> publish selector (bypass)
  log "PGM PUBLISH DROPPED (selector advancing, mediamtx 0 bytes) — republishing encoder on ${src}"
  $DOCKER restart "$RELAY_CTR" >/dev/null 2>&1; sleep 7
  _post 9601 /pipeline/start "{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$src\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30}}" >/dev/null
  log "encoder republished"
}

pass(){ heal_selector; heal_relay; heal_publish; }

if [ "${1:-}" = "--watch" ]; then
  log "watching (every ${INTERVAL}s) — selector + relay"
  while :; do pass; sleep "$INTERVAL"; done
else
  pass
fi
