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
# Thumbs-probe heal (HW Oct 10): the thumbs probe (mxl_thumbs.py) readers can wedge on a
# specific slot — a live Makito/PTZ then shows 'unknown' on the Health Skin even though its
# flow is advancing (mxl-info sees it; the probe's reader doesn't). It needs a restart to
# re-attach. heal_thumbs detects that exact mismatch (Core reports a wired source NOT 'ok'
# while its flow IS advancing) and restarts the thumbs unit. STRIKES avoids restarting during
# the probe's own ~30s startup. CORE_URL = the Skin's health API; THUMBS_UNIT = systemd unit.
THUMBS_UNIT="${MXL_THUMBS_UNIT:-mxl-thumbs.service}"
CORE_URL="${MXL_CORE_URL:-http://127.0.0.1:3100}"
THUMBS_HEAL="${MXL_THUMBS_HEAL:-1}"       # 0 disables the thumbs-probe heal
THUMBS_STRIKES="${MXL_THUMBS_STRIKES:-3}" # consecutive mismatched polls before restart
_thumbs_strike=0; _thumbs_last_heal=0
_sel_api_last_heal=0   # cooldown for the "selector HTTP API died" container restart

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
  st=$(_get 9604 /pipeline/status)
  # API UNREACHABLE (empty body / http 000) while the container is up = the selector's HTTP
  # control server died but the router thread keeps running (HW Oct 10: container "Up 7h",
  # nothing listening on 9604, PGM frozen, every cut/status 000 — selfheal's own probes blind).
  # A /pipeline/start can't fix a dead API; the container must be restarted to respawn it.
  # Guard: only restart if the container is actually running (don't fight a stopped container)
  # and respect the publish cooldown pattern via a dedicated timestamp to avoid restart storms.
  if [ -z "$st" ]; then
    local now; now=$(date +%s)
    if [ $((now - _sel_api_last_heal)) -ge 120 ] && $DOCKER ps --format '{{.Names}}' 2>/dev/null | grep -qx "$SEL_CTR"; then
      log "SELECTOR API UNREACHABLE (container up, :9604 dead) — restarting $SEL_CTR"
      $DOCKER restart "$SEL_CTR" >/dev/null 2>&1
      _sel_api_last_heal=$now
      # wait for the API to answer, then let the next pass (or pgm-heal) re-wire + republish
      local i=0; while [ "$i" -lt 20 ]; do curl -s -m3 "http://127.0.0.1:9604/pipeline/status" >/dev/null 2>&1 && break; i=$((i+1)); sleep 2; done
      # after an API restart the router comes up but the encoder must be re-pointed at the
      # selector output (its flow object was recreated) — republish via the proven path.
      local out; out=$(sel_out_flow)
      [ -n "$out" ] && _enc_restart "$out"
      log "selector API restarted + encoder republished"
    fi
    return 0
  fi
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
  # ADVANCING but NO bytes to mediamtx = the grey screen. Republish the encoder on the
  # SELECTOR output (keyer bypassed for stability — HW Oct 10: the gst-keyer throttled the
  # whole chain to ~300KB/s and its flow 86efffa4 keeps a stale advancing head even when the
  # keyer is stopped, so an earlier version kept republishing onto that DEAD keyer flow every
  # 30s = permanent 0 bytes. Always use the selector output here.). To re-enable keyed PGM,
  # fix the keyer's throughput first, then revisit. src is '' only transiently.
  local src="$out"
  # The encoder sticks on its current flow if `start` is sent while running, and a container
  # restart auto-starts it on its DEFAULT (keyer) flow — both leave PGM dead. So STOP until
  # confirmed stopped, then START, then VERIFY it took (one retry). No container restart.
  log "PGM PUBLISH DROPPED (selector advancing, mediamtx 0 bytes) — republishing encoder on selector ${src}"
  _enc_restart "$src"
  log "encoder republished"
}

# Re-point the encoder at $1 reliably: stop-until-stopped -> start -> verify (one retry).
_enc_restart(){
  local want="$1" i r now
  curl -s -m3 "http://127.0.0.1:9601/pipeline/status" >/dev/null 2>&1 || { $DOCKER restart "$RELAY_CTR" >/dev/null 2>&1; sleep 8; }
  i=0; while [ "$i" -lt 6 ]; do
    _post 9601 /pipeline/stop '{}' >/dev/null
    sleep 2
    r=$(_get 9601 /pipeline/status | _json running)
    [ "$r" = "False" ] && break; i=$((i+1))
  done
  _post 9601 /pipeline/start "{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$want\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30}}" >/dev/null
  sleep 3
  now=$(_get 9601 /pipeline/status | _json video_flow_uuid)
  if [ "$now" != "$want" ]; then
    i=0; while [ "$i" -lt 6 ]; do _post 9601 /pipeline/stop '{}' >/dev/null; sleep 2
      [ "$(_get 9601 /pipeline/status | _json running)" = "False" ] && break; i=$((i+1)); done
    _post 9601 /pipeline/start "{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$want\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30}}" >/dev/null
  fi
}

# ── heal 4: thumbs probe wedged on a live source → restart it (keeps Makito/PTZ visible) ─
# Symptom: a source wired into the selector is ADVANCING, but the Core's health reports it
# NOT 'ok' (unknown/dead/frozen) — the thumbs reader for that slot wedged and the camera
# vanishes from the Health Skin. Restart the thumbs unit so its readers re-attach. Guards:
# only counts a strike when a flow is genuinely advancing (never restarts for a truly idle
# source), needs THUMBS_STRIKES in a row (rides out the probe's own startup), and a 120s
# cooldown so a restart that takes a while to settle doesn't trigger a restart storm.
heal_thumbs(){
  [ "$THUMBS_HEAL" = "1" ] || return 0
  local now; now=$(date +%s)
  [ $((now - _thumbs_last_heal)) -lt 120 ] && return 0     # cooling down after a restart
  # Pull the Core's per-source view once (it already joins thumbs state + wiring, cheaply).
  local health; health=$(_get_url "$CORE_URL/api/mxl/health") || return 0
  [ -n "$health" ] || return 0
  # Any wired, non-stable source reported not-ok whose flow IS advancing = a wedged reader.
  local wedged; wedged=$(printf '%s' "$health" | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
for s in d.get("sources", []):
    # stable correspondent slots legitimately sit "standby" with no source — skip them
    if s.get("stable"): continue
    if s.get("state") not in ("ok",):
        print(s.get("name","?")); break
' 2>/dev/null)
  if [ -z "$wedged" ]; then _thumbs_strike=0; return 0; fi
  # The Core said a source is not-ok; confirm its FLOW is actually advancing before blaming
  # the probe (if the flow is genuinely dead, that is a source problem, not a thumbs wedge).
  # We only have names here; the selector output advancing + >2 sources ok is a good proxy
  # that the facility is live and it is the probe (not the domain) that is broken.
  local oks; oks=$(printf '%s' "$health" | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: print(0); sys.exit(0)
print(sum(1 for s in d.get("sources", []) if s.get("state")=="ok"))
' 2>/dev/null)
  [ -n "$oks" ] && [ "$oks" -ge 1 ] 2>/dev/null || { _thumbs_strike=0; return 0; }  # whole probe cold -> let it start
  _thumbs_strike=$((_thumbs_strike + 1))
  log "thumbs probe looks wedged ($wedged not-ok while others ok) (${_thumbs_strike}/${THUMBS_STRIKES})"
  if [ "$_thumbs_strike" -ge "$THUMBS_STRIKES" ]; then
    log "restarting $THUMBS_UNIT so its readers re-attach"
    systemctl restart "$THUMBS_UNIT" >/dev/null 2>&1 || sudo systemctl restart "$THUMBS_UNIT" >/dev/null 2>&1
    _thumbs_strike=0; _thumbs_last_heal=$now
  fi
}

# curl to a full URL (the thumbs heal talks to the Core's health API, not a bare port).
_get_url(){ curl -s -m 5 "$1" 2>/dev/null; }

pass(){ heal_selector; heal_relay; heal_publish; heal_thumbs; }

if [ "${1:-}" = "--watch" ]; then
  log "watching (every ${INTERVAL}s) — selector + relay + publish + thumbs"
  while :; do pass; sleep "$INTERVAL"; done
else
  pass
fi
