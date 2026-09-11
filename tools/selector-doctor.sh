#!/bin/bash
# selector-doctor.sh — auto-heal the wedge where the input-selector container's
# CONTROL API goes dead (times out) while the container spins ~100% CPU.
# This is the 2026-09-11 14:02 incident class: the selector API is what every
# recovery path (cut, /api/mxl/repair) calls, so when IT wedges nothing else
# can self-heal — a ~7-min public outage until a human docker-restarts it.
#
# Detection: N consecutive control-API timeouts. Heal: docker restart the
# container, wait for the API, then trigger the backend repair cascade so all
# slots re-attach. Cooldown prevents restart loops.
#
# Runs as a systemd service on VM1 (host bash). Reads nothing; pure poller.
set -u
API=http://127.0.0.1:9604/pipeline/status
BACKEND=https://prodbots.com/api/mxl/repair   # UA header dodges CF bot rule
UA='mxl-selector-doctor/1.0'
FAILS=0
FAIL_LIMIT=3          # ~3 x (timeout+interval) ≈ 20s wedged before acting
INTERVAL=6
last_heal=0
COOLDOWN=180

log(){ echo "$(date -u +%H:%M:%S) $*"; }

while true; do
  if curl -s -m 4 -o /dev/null "$API"; then
    [ "$FAILS" -gt 0 ] && log "selector API recovered after $FAILS misses"
    FAILS=0
  else
    FAILS=$((FAILS+1))
    log "selector API timeout ($FAILS/$FAIL_LIMIT)"
    if [ "$FAILS" -ge "$FAIL_LIMIT" ]; then
      now=$(date +%s)
      if [ $((now - last_heal)) -lt "$COOLDOWN" ]; then
        log "in cooldown ($(( COOLDOWN - (now-last_heal) ))s left) — not restarting again yet"
      else
        log "HEAL: docker restart input-selector"
        sudo docker restart input-selector >/dev/null 2>&1
        last_heal=$now
        # wait up to 40s for the API to answer again
        for i in $(seq 1 20); do sleep 2; curl -s -m 3 -o /dev/null "$API" && break; done
        log "selector back — triggering repair cascade"
        curl -s -m 30 -X POST -H "Content-Type: application/json" -H "User-Agent: $UA" -d '{}' "$BACKEND" >/dev/null 2>&1 || true
        FAILS=0
      fi
    fi
  fi
  sleep "$INTERVAL"
done
