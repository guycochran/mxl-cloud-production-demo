#!/usr/bin/env sh
# mxl-pgm-heal — bring the whole PGM/output chain to a known-good state after a (re)boot.
#
# WHY: a facility bring-up (quickstart) starts the containers, but the SELECTOR wiring + the
# mxl2webrtc publish need to settle in the right ORDER, each AFTER its upstream is live, or a remote
# viewer gets grey/frozen (HW lessons, Oct 8). With the CEF keyer BYPASSED (MXL_KEYER_ENABLE=0,
# encoder reads the selector directly), the heal is: wait for live source flows -> wire them into the
# selector (so all are cuttable) -> make the encoder read the selector output -> republish mxl2webrtc
# -> verify the selector output is readable + advancing (real moving frames to viewers).
#
# Idempotent + verification-driven. Ordered LAST (After=everything). POSIX sh.
#
# RUNTIME UUID DISCOVERY (no hardcoded flow ids): every source is named by its ROLE
# (Pattern, Playout, Guest1..GuestN, Guest5Stable/Guest6Stable, Keyer, Selector) and the
# UUID is resolved at runtime from `mxl-info -l` via tools/mxl-flows.sh. This is what makes
# the script facility-portable: on any box it wires whatever sources are actually present,
# instead of 400ing on another facility's phantom UUIDs (reviewer issue #2).
set -u

DOMAIN="${MXL_DOMAIN:-/mxl-domain}"
SEL_PORT="${MXL_SELECTOR_PORT:-9604}"; ENC_PORT="${MXL_ENCODER_PORT:-9601}"
SEL_CTR="${SELECTOR_CONTAINER:-input-selector}"

# docker directly if we can (root), else sudo docker.
if docker ps >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi

# discovery helpers (flow_by_role / flow_present / all_video_roles / mxl_flows_refresh)
_here=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$_here/mxl-flows.sh"

# Fixed studio sources stay on their VOLATILE flow (they don't reconnect mid-show). The
# roving-correspondent slots prefer their STABLE flow (never-interrupt, docs/NEVER-INTERRUPT-PGM.md):
# a stabilizer (mxl-stabilizer@guestN) owns GuestNStable, created once at boot and kept alive across
# the correspondent connecting/dropping, so a join NEVER forces a selector re-wire -> PGM never drops.
# Discovered-by-role, so a facility with different guest counts just works.
FIXED_ROLES="${MXL_FIXED_ROLES:-Pattern Playout Guest1 Guest2 Guest3 Guest4}"
STABLE_ROLES="${MXL_STABLE_ROLES:-Guest5 Guest6}"   # prefer GuestNStable, else fall back to GuestN

log(){ echo "mxl-pgm-heal: $*"; }
headidx(){ $DOCKER exec "$SEL_CTR" sh -c "/opt/mxl/tools/mxl-info/mxl-info -d $DOMAIN -f $1 2>/dev/null" 2>/dev/null | grep -i 'Head index' | grep -oE '[0-9]+' | head -1; }
# advancing: is a flow actually MOVING right now? (costs a 1s sample). A stable flow that
# exists but freewheeled dark reads "not advancing" — so we must never CUT to it, only PRE-WIRE
# it. The default program cut uses this to avoid the dead-source blank (HW Oct 10 bug).
advancing(){ a=$(headidx "$1"); sleep 1; b=$(headidx "$1"); [ -n "$a" ] && [ -n "$b" ] && [ "$b" -gt "$a" ] 2>/dev/null; }

# 1. wait for the selector to answer + the always-on Pattern source to exist
i=0
while [ "$i" -lt 30 ]; do
  mxl_flows_refresh
  curl -s -m3 "http://127.0.0.1:${SEL_PORT}/pipeline/status" >/dev/null 2>&1 && flow_present Pattern && break
  i=$((i+1)); sleep 2
done
mxl_flows_refresh
PATTERN=$(flow_by_role Pattern Video)
[ -n "$PATTERN" ] || { log "FATAL: Pattern flow never appeared (facility not up?)"; exit 1; }

# 2. build the input list from PRESENT roles. Pattern is slot 0 (always-on fallback). Fixed
# studio sources on their volatile flow; correspondent slots prefer GuestNStable, else GuestN.
# SLOTS[] tracks the uuid each wired slot carries, so the default-cut below tests the RIGHT flow.
INPUTS="\"$PATTERN\""
SLOTS="$PATTERN"
add_uuid(){ INPUTS="$INPUTS,\"$1\""; SLOTS="$SLOTS $1"; }
for role in $FIXED_ROLES; do
  [ "$role" = "Pattern" ] && continue        # already slot 0
  u=$(flow_by_role "$role" Video); [ -n "$u" ] && add_uuid "$u"
done
for role in $STABLE_ROLES; do
  u=$(flow_by_role "${role}Stable" Video)     # prefer the stable (never-interrupt) flow
  [ -n "$u" ] || u=$(flow_by_role "$role" Video)   # fall back to the volatile flow
  [ -n "$u" ] && add_uuid "$u"
done
log "wiring selector: $INPUTS"
curl -s -m8 -X POST "http://127.0.0.1:${SEL_PORT}/pipeline/start" -H 'Content-Type: application/json' \
  -d "{\"domain_path\":\"$DOMAIN\",\"input_flow_uuids\":[$INPUTS],\"grouphint\":\"Selector\",\"description\":\"program out\",\"label\":\"Selector PGM\"}" \
  -o /dev/null -w 'selector wire -> %{http_code}\n' | sed 's/^/mxl-pgm-heal: /'
sleep 2
# default program on the first slot that is ACTUALLY ADVANCING (not merely present) — a
# dead/idle source (incl. a freewheeled-dark stable flow) would blank PGM (HW Oct 10 bug).
# Walk slots in order; pattern(0) is always-on so it's the guaranteed fallback.
CUT=0; idx=0
for u in $SLOTS; do
  if [ "$idx" -gt 0 ] && advancing "$u"; then CUT=$idx; break; fi
  idx=$((idx+1))
done
curl -s -m5 -X POST "http://127.0.0.1:${SEL_PORT}/pipeline/active-input" -H 'Content-Type: application/json' -d "{\"slot\":$CUT}" >/dev/null 2>&1
log "program cut to slot $CUT (verified advancing, else pattern)"

# 3. the encoder must read the SELECTOR output (keyer bypass). Get the live selector out flow.
SEL_OUT=$(curl -s -m4 "http://127.0.0.1:${SEL_PORT}/pipeline/status" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("output_flow_uuid",""))' 2>/dev/null)
[ -n "$SEL_OUT" ] || { log "FATAL: no selector output flow"; exit 1; }
log "selector output = $SEL_OUT"

# 3b. KEYER source selection. MXL_KEYER_ENABLE=1 means the operator WANTS the gst-keyer
# lower-third on program; wait (with retries) for Keyer-out to be advancing before wiring
# the encoder to it, so a brief gap during the program cut doesn't drop graphics to bypass.
# ⚠️ Re-enabling the keyer needs its throughput fixed first (HW Oct 10: it throttled the whole
# chain to ~300KB/s and its flow keeps a stale advancing head even when stopped). Default OFF.
ENC_SRC="$SEL_OUT"
if [ "${MXL_KEYER_ENABLE:-0}" = "1" ]; then
  KEYER_OUT=$(flow_by_role Keyer Video)
  if [ -n "$KEYER_OUT" ]; then
    t=0
    while [ "$t" -lt 8 ]; do
      if advancing "$KEYER_OUT"; then
        ENC_SRC="$KEYER_OUT"; log "keyer-out advancing -> encoder reads KEYED program (lower-third ON)"; break
      fi
      t=$((t+1))
    done
    [ "$ENC_SRC" = "$KEYER_OUT" ] || log "MXL_KEYER_ENABLE=1 but keyer-out never advanced after 8s -> bypass (check mxl-gst-keyer.service)"
  else
    log "MXL_KEYER_ENABLE=1 but no Keyer flow present -> bypass"
  fi
else
  log "keyer disabled (MXL_KEYER_ENABLE!=1) -> encoder reads selector (bypass)"
fi
# 4. restart mxl2webrtc fresh, then re-point it at ENC_SRC. Two HW-learned hazards (Oct 10):
#   (a) a fixed `sleep 10` after `docker restart` races the container's API coming up -> the
#       start POST returns 000 and the encoder is left reading its stale/default flow (the
#       keyer, which may be dead) -> PGM grey. So WAIT for the API to actually answer.
#   (b) a `start` while the pipeline is already `running` does NOT switch the flow -> the
#       encoder sticks on the old flow (reported running:true, video_flow_uuid unchanged). So
#       STOP until running:false BEFORE the start, and VERIFY the start actually took.
# Only HARD-restart the container if its API is unreachable. A `docker restart` makes the
# encoder auto-start on its DEFAULT flow (the keyer), which then races our stop/start below
# and can leave PGM reading a dead keyer -> grey (HW Oct 10). If the API already answers,
# a stop-until-stopped + start is cleaner and switches the flow reliably.
if curl -s -m3 "http://127.0.0.1:${ENC_PORT}/pipeline/status" >/dev/null 2>&1; then
  log "mxl2webrtc API up — re-pointing without a container restart"
else
  log "mxl2webrtc API down — restarting container"
  docker restart mxl2webrtc >/dev/null 2>&1 || sudo docker restart mxl2webrtc >/dev/null 2>&1
  j=0; while [ "$j" -lt 20 ]; do
    curl -s -m3 "http://127.0.0.1:${ENC_PORT}/pipeline/status" >/dev/null 2>&1 && break
    j=$((j+1)); sleep 2
  done
fi
# stop until confirmed stopped (so the flow switch below actually applies)
s=0; while [ "$s" -lt 6 ]; do
  curl -s -m8 -X POST "http://127.0.0.1:${ENC_PORT}/pipeline/stop" -d '{}' -o /dev/null 2>/dev/null
  sleep 2
  r=$(curl -s -m4 "http://127.0.0.1:${ENC_PORT}/pipeline/status" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("running"))' 2>/dev/null)
  [ "$r" = "False" ] && break
  s=$((s+1))
done
curl -s -m12 -X POST "http://127.0.0.1:${ENC_PORT}/pipeline/start" -H 'Content-Type: application/json' \
  -d "{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$ENC_SRC\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30}}" \
  -o /dev/null -w 'encoder start -> %{http_code}\n' | sed 's/^/mxl-pgm-heal: /'
sleep 3
# verify the start TOOK (encoder is reading what we asked); one retry if it stuck
_enc_now=$(curl -s -m4 "http://127.0.0.1:${ENC_PORT}/pipeline/status" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("video_flow_uuid",""))' 2>/dev/null)
if [ "$_enc_now" != "$ENC_SRC" ]; then
  log "encoder stuck on ${_enc_now:-none} (wanted $ENC_SRC) — forcing stop+start once more"
  t=0; while [ "$t" -lt 6 ]; do curl -s -m8 -X POST "http://127.0.0.1:${ENC_PORT}/pipeline/stop" -d '{}' -o /dev/null 2>/dev/null; sleep 2
    [ "$(curl -s -m4 "http://127.0.0.1:${ENC_PORT}/pipeline/status" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("running"))' 2>/dev/null)" = "False" ] && break; t=$((t+1)); done
  curl -s -m12 -X POST "http://127.0.0.1:${ENC_PORT}/pipeline/start" -H 'Content-Type: application/json' \
    -d "{\"domain_path\":\"$DOMAIN\",\"video_flow_uuid\":\"$ENC_SRC\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30}}" \
    -o /dev/null -w 'encoder start (retry) -> %{http_code}\n' | sed 's/^/mxl-pgm-heal: /'
fi

# 5. verify: mxl2webrtc published + selector output is ADVANCING (real moving frames)
i=0; PUB=0
while [ "$i" -lt 10 ]; do
  docker logs --since 40s mxl2webrtc 2>&1 | grep -q 'streaming to MediaMTX' && { PUB=1; break; }
  sudo docker logs --since 40s mxl2webrtc 2>&1 | grep -q 'streaming to MediaMTX' && { PUB=1; break; }
  i=$((i+1)); sleep 3
done
[ "$PUB" = 1 ] && log "mxl2webrtc PUBLISHED to mediamtx" || log "WARN: mxl2webrtc publish not confirmed in log window"
h0=$(headidx "$SEL_OUT"); sleep 3; h1=$(headidx "$SEL_OUT")
if [ -n "$h0" ] && [ -n "$h1" ] && [ "$h1" -gt "$h0" ] 2>/dev/null; then
  log "OK — selector output ADVANCING ($h0 -> $h1); PGM is live + moving"
else
  log "WARN: selector output not clearly advancing ($h0 -> $h1)"
fi
log "done"
