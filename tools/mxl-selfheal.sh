#!/usr/bin/env bash
# mxl-selfheal.sh — a portable self-healer for the quickstart MXL switcher.
#
# The adopter stack has no watchdog (the live mxlswitcher.com facility does, but
# those healers are wired to prodbots/VM1 — see mxl-doctor). Yet the facility drifts:
# across a long session we repeatedly hit two failures, both of which black out the
# program with no automatic recovery:
#
#   1. SELECTOR DOWN — the input-selector pipeline stops (running:false, inputs:[]),
#      so every cut returns 409 "not attached" and the program freezes.
#   2. RELAY WAITING FOR AUDIO — mxl2webrtc defaults to mode:video+audio and waits
#      forever for an audio_pgm flow that a bare quickstart never starts, so the
#      WebRTC program monitor shows "stream not found" even though video exists.
#
# This watcher polls the LOCAL control ports (no backend, no prodbots) and fixes
# exactly those two, idempotently. Safe to run as a plain loop, a systemd unit, or
# a cron. It only ever acts when something is actually broken.
#
#   tools/mxl-selfheal.sh            # one check+heal pass, then exit
#   tools/mxl-selfheal.sh --watch    # loop every MXL_SELFHEAL_INTERVAL (default 15s)
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

log(){ printf '%s mxl-selfheal: %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
_get(){ curl -s -m 5 "http://127.0.0.1:$1$2" 2>/dev/null; }
_post(){ curl -s -m 25 -X POST -H 'Content-Type: application/json' -d "$3" "http://127.0.0.1:$1$2" 2>/dev/null; }
_json(){ python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1',''))" 2>/dev/null; }

# selector inputs to restore: prefer the generated manifest, else re-discover the
# two base sources by label (same method quickstart used).
selector_inputs_json(){
  if [ -n "${MXL_FACILITY_JSON:-}" ] && [ -f "$MXL_FACILITY_JSON" ]; then
    python3 - "$MXL_FACILITY_JSON" <<'PY' 2>/dev/null && return 0
import json, sys
d = json.load(open(sys.argv[1]))
vf = d.get("video_flows", {})
roles = d.get("program", {}).get("selector_inputs") or d.get("program", {}).get("layout_inputs") or []
uuids = [vf[r]["uuid"] for r in roles if r in vf]
print(json.dumps(uuids))
PY
  fi
  # fallback: discover the pattern + clip flows live
  local pat clip
  pat=$(docker exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
    | grep -E "^\s+Video : [0-9a-f-]{36} - Pattern Video\$" | grep -oE '[0-9a-f-]{36}' | head -1)
  clip=$(docker exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
    | grep -E "^\s+Video : [0-9a-f-]{36} - Clip Video\$" | grep -oE '[0-9a-f-]{36}' | head -1)
  [ -n "$pat" ] && [ -n "$clip" ] && printf '["%s","%s"]' "$pat" "$clip"
}

keyer_out_flow(){
  [ -n "$KEYER_FLOW" ] && { printf '%s' "$KEYER_FLOW"; return; }
  KEYER_FLOW=$(docker exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -l" 2>/dev/null \
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
  st=$(docker exec "$RELAY_CTR" sh -c "curl -s -m5 http://127.0.0.1:9600/pipeline/status" 2>/dev/null) || return 0
  running=$(printf '%s' "$st" | _json running)
  mode=$(printf '%s' "$st" | _json mode)
  # broken state = not running, OR running in a mode that waits for an absent audio flow
  if [ "$running" = "True" ] && [ "$mode" = "video" ]; then return 0; fi
  local key; key=$(keyer_out_flow)
  [ -n "$key" ] || { log "relay unhealthy but keyer flow not found yet — skipping"; return 0; }
  log "RELAY unhealthy (running=$running mode=$mode) — restarting VIDEO-ONLY on keyer $key"
  docker exec "$RELAY_CTR" sh -c "curl -s -m5 -X POST http://127.0.0.1:9600/pipeline/stop >/dev/null; sleep 1; \
    curl -s -m8 -X POST -H 'Content-Type: application/json' \
    -d '{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$key\",\"audio_flow_uuid\":null,\"mode\":\"video\"}' \
    http://127.0.0.1:9600/pipeline/start >/dev/null" 2>/dev/null
  log "relay restarted video-only"
}

pass(){ heal_selector; heal_relay; }

if [ "${1:-}" = "--watch" ]; then
  log "watching (every ${INTERVAL}s) — selector + relay"
  while :; do pass; sleep "$INTERVAL"; done
else
  pass
fi
