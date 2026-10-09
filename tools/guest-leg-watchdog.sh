#!/usr/bin/env bash
# guest-leg-watchdog — keeps each guest's SRT ingest chain healthy so a contributor
# (phone/Larix) can always connect.
#
# THE WEDGE IT HEALS (HW Oct 9): each guest ingest is a 3-container chain —
#   <name>-fanout  (public SRT listener :PUB)  → splits A/V to two internal legs
#   <name>         (video core, listens :VLEG) → writes the guest VIDEO flow
#   <name>-audio   (audio core, listens :ALEG) → writes the guest AUDIO flow
# guest_av_listen.sh writes to BOTH legs; if EITHER leg's listener is down, the
# fanout's SRT write fails ("Socket is broken or closed, Trying to reconnect") and it
# REJECTS the whole incoming connection — so a phone (and even a clean local caller)
# gets "Input/output error" and never appears. Seen live: guest2 audio leg :9091 dead,
# guest1 audio leg :9090 dead — both silently blocking joins.
#
# This watchdog polls every guest's three ports; if a chain is incomplete (public up but
# a leg missing, or vice versa) it restarts that chain in order (audio → core → fanout)
# and logs it. A chain with ALL ports down is treated as "not provisioned" and skipped
# (guest4-6 may legitimately not exist on a smaller deployment).
#
# Config (env-overridable; defaults match the box):
#   GUEST_PORTS   per-guest "name:pub:vleg:aleg" specs, comma-separated
#   POLL_SECONDS  default 20
#   COOLDOWN_SECONDS  min seconds between restarts of the SAME chain (default 60)
set -u

POLL_SECONDS=${POLL_SECONDS:-20}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-60}

# name:public:video-leg:audio-leg  (the box's scheme; guest1/2 contiguous, 3-6 bolt-ons)
GUEST_PORTS=${GUEST_PORTS:-"guest1:8890:8990:9090,guest2:8891:8991:9091,guest3:8895:8995:9095,guest4:8896:8996:9096,guest5:8897:8997:9097,guest6:8898:8998:9098"}

declare -A last_heal

bound() { ss -ulnp 2>/dev/null | grep -q ":$1 "; }   # is a UDP port listening?

heal_chain() {
  local name=$1
  local now; now=$(date +%s)
  local last=${last_heal[$name]:-0}
  if [ $((now - last)) -lt "$COOLDOWN_SECONDS" ]; then
    logger -t guest-leg-watchdog "skip $name — in cooldown ($((now - last))s < ${COOLDOWN_SECONDS}s)"
    return
  fi
  last_heal[$name]=$now
  logger -t guest-leg-watchdog "HEALING $name chain (a leg was down) — restart audio→core→fanout"
  # order matters: the cores (leg listeners) must be up before the fanout tries to write
  docker restart "${name}-audio" "${name}" "${name}-fanout" >/dev/null 2>&1
}

logger -t guest-leg-watchdog "started — watching: $GUEST_PORTS (poll ${POLL_SECONDS}s)"
while :; do
  IFS=',' read -ra specs <<< "$GUEST_PORTS"
  for spec in "${specs[@]}"; do
    IFS=':' read -r name pub vleg aleg <<< "$spec"
    # skip guests that don't exist as containers on this deployment
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${name}-fanout" || continue

    up=0
    bound "$pub"  && up=$((up+1))
    bound "$vleg" && up=$((up+1))
    bound "$aleg" && up=$((up+1))

    # 0 up = container exists but nothing bound (starting up) → give it a cycle, don't thrash
    # 3 up = healthy. 1 or 2 up = a leg wedged → heal.
    if [ "$up" -eq 1 ] || [ "$up" -eq 2 ]; then
      logger -t guest-leg-watchdog "$name degraded: pub=$(bound "$pub" && echo up || echo DOWN) vleg=$(bound "$vleg" && echo up || echo DOWN) aleg=$(bound "$aleg" && echo up || echo DOWN)"
      heal_chain "$name"
    fi
  done
  sleep "$POLL_SECONDS"
done
