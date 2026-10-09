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
# a leg missing, or vice versa) PERSISTENTLY it restarts that chain in order
# (audio → core → fanout) and logs it. A chain with ALL ports down is treated as "not
# provisioned" and skipped (guest4-6 may legitimately not exist on a smaller deployment).
#
# IMPORTANT — don't fight the idle cycle: when a guest has NO publisher, its audio/video
# core wrapper loops (`while :; do python3 guest_*.py ...; sleep 3; done`) and the SRT
# source exits "not-linked (-1)" each cycle, so the leg listener briefly unbinds between
# cycles. That's NORMAL for an idle slot — NOT a wedge. So we only heal a leg that stays
# down across STRIKES consecutive polls; a one-poll blip (the idle cycle) is ignored.
#
# Config (env-overridable; defaults match the box):
#   GUEST_PORTS   per-guest "name:pub:vleg:aleg" specs, comma-separated
#   POLL_SECONDS  default 20
#   STRIKES       consecutive degraded polls before healing (default 3 → ~60s of
#                 persistent degrade, well past any idle-cycle blip)
#   COOLDOWN_SECONDS  min seconds between restarts of the SAME chain (default 90)
set -u

POLL_SECONDS=${POLL_SECONDS:-20}
STRIKES=${STRIKES:-3}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-90}
declare -A strike

# name:public:video-leg:audio-leg  (the box's scheme; guest1/2 contiguous, 3-6 bolt-ons)
GUEST_PORTS=${GUEST_PORTS:-"guest1:8890:8990:9090,guest2:8891:8991:9091,guest3:8895:8995:9095,guest4:8896:8996:9096,guest5:8897:8997:9097,guest6:8898:8998:9098"}

declare -A last_heal
declare -A heal_count
# GIVE_UP: after this many heals that DON'T make a chain healthy, stop restarting it and
# just log — some legs flap for reasons a restart can't fix (e.g. a hardware encoder that
# sends video-only, so its audio core cycles 'not-linked' forever). Thrashing it endlessly
# only churns CPU. A chain that goes healthy resets its count, so transient wedges always
# get healed; only a chronic one is abandoned.
GIVE_UP=${GIVE_UP:-4}

bound() { ss -ulnp 2>/dev/null | grep -q ":$1 "; }   # is a UDP port listening?

heal_chain() {
  local name=$1
  local now; now=$(date +%s)
  local last=${last_heal[$name]:-0}
  if [ $((now - last)) -lt "$COOLDOWN_SECONDS" ]; then
    logger -t guest-leg-watchdog "skip $name — in cooldown ($((now - last))s < ${COOLDOWN_SECONDS}s)"
    return
  fi
  heal_count[$name]=$(( ${heal_count[$name]:-0} + 1 ))
  if [ "${heal_count[$name]}" -gt "$GIVE_UP" ]; then
    logger -t guest-leg-watchdog "GIVE UP on $name — healed ${heal_count[$name]}x without it staying healthy; leaving it alone (restart won't fix). Investigate manually."
    last_heal[$name]=$now   # keep cooldown so the give-up message doesn't spam
    return
  fi
  last_heal[$name]=$now
  logger -t guest-leg-watchdog "HEALING $name chain (heal #${heal_count[$name]}) — restart audio→core→fanout"
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

    # 0 up = container exists but nothing bound (starting up / idle between cycles) → not
    #         a wedge; reset strikes. 3 up = healthy; reset. 1-2 up = PARTIAL — count a
    #         strike, and only heal once it's been partial for STRIKES consecutive polls
    #         (so the normal idle-cycle unbind, which clears within a poll, never heals).
    if [ "$up" -eq 1 ] || [ "$up" -eq 2 ]; then
      strike[$name]=$(( ${strike[$name]:-0} + 1 ))
      logger -t guest-leg-watchdog "$name degraded (${strike[$name]}/${STRIKES}): pub=$(bound "$pub" && echo up || echo DOWN) vleg=$(bound "$vleg" && echo up || echo DOWN) aleg=$(bound "$aleg" && echo up || echo DOWN)"
      if [ "${strike[$name]}" -ge "$STRIKES" ]; then
        heal_chain "$name"
        strike[$name]=0
      fi
    elif [ "$up" -eq 3 ]; then
      strike[$name]=0
      heal_count[$name]=0   # healthy again → forgive past heals (transient wedge recovered)
    else
      strike[$name]=0       # 0 up = absent/starting; don't count heals against it
    fi
  done
  sleep "$POLL_SECONDS"
done
