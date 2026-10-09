#!/usr/bin/env bash
# guest-leg-watchdog — conservatively keeps a guest's PUBLIC SRT listener available so a
# contributor (phone/Larix) can connect to a FREE slot.
#
# ⚠️ HARD LESSON (HW Oct 9): restarting a guest chain RECREATES its MXL flows, which forces
# the input-selector to RE-WIRE (router thread restarts). Do that every few seconds and the
# selector never stabilizes → PGM sticks, cuts fail, multiview thumbnails flap. The first
# version of this watchdog restarted a chain whenever any internal leg blipped (which is
# NORMAL for an idle slot, and ALSO for a video-only hardware encoder whose audio core
# cycles 'not-linked' forever) — so it churned the selector and broke the whole facility.
#
# So this version is deliberately MINIMAL and SAFE:
#   • It only cares about the PUBLIC listener (:PUB) — the thing a contributor connects to.
#     Internal legs flapping is NOT our business (that's the idle cycle / source quirks).
#   • It NEVER restarts a chain whose VIDEO flow is advancing (a live source → hands off).
#   • It only restarts a chain that is BOTH: public port missing AND video flow not
#     advancing AND stayed that way across STRIKES consecutive polls — i.e. a genuinely
#     dead listener on an idle slot, the only case that silently blocks a new join.
#   • It gives up after GIVE_UP restarts that don't help, and has a long cooldown.
#   • OFF by default. Enable deliberately: ENABLE=1 (or the systemd unit sets it).
#
# Honestly: on this box the better fix is usually to leave guests alone and let a CONNECTING
# contributor's SRT handshake wake the listener. Use this only if idle listeners are
# observed to die outright. Config (env):
#   ENABLE=1            required to do anything (default 0 = observe-only, just logs)
#   GUEST_PORTS         "name:pub:vleg:aleg" specs, comma-separated
#   POLL_SECONDS=30  STRIKES=4  GIVE_UP=3  COOLDOWN_SECONDS=300
set -u

ENABLE=${ENABLE:-0}
POLL_SECONDS=${POLL_SECONDS:-30}
STRIKES=${STRIKES:-4}
GIVE_UP=${GIVE_UP:-3}
COOLDOWN_SECONDS=${COOLDOWN_SECONDS:-300}
SELECTOR_CONTAINER=${SELECTOR_CONTAINER:-input-selector}
DOMAIN=${MXL_DOMAIN:-/mxl-domain}
MXL_INFO=${MXL_INFO:-/opt/mxl/tools/mxl-info/mxl-info}

# name:public:video-leg:audio-leg : video-flow-uuid
GUEST_PORTS=${GUEST_PORTS:-"guest1:8890:8990:9090:9e111e00-aaaa-4bbb-8ccc-000000000001,guest2:8891:8991:9091:9e222e00-aaaa-4bbb-8ccc-000000000001,guest3:8895:8995:9095:9e333e00-aaaa-4bbb-8ccc-000000000001,guest4:8896:8996:9096:9e444e00-aaaa-4bbb-8ccc-000000000001,guest5:8897:8997:9097:9e555e00-aaaa-4bbb-8ccc-000000000001,guest6:8898:8998:9098:9e666e00-aaaa-4bbb-8ccc-000000000001"}

declare -A last_heal heal_count strike

bound() { ss -ulnp 2>/dev/null | grep -q ":$1 "; }

head_idx() {  # current head index of a flow, or empty
  docker exec "$SELECTOR_CONTAINER" sh -c "$MXL_INFO -d $DOMAIN -f $1 2>/dev/null" 2>/dev/null \
    | grep -i 'Head index' | grep -oE '[0-9]+' | head -1
}
video_advancing() {  # is the guest's VIDEO flow moving? (a live source — never touch it)
  local uuid=$1 a b
  a=$(head_idx "$uuid"); sleep 1; b=$(head_idx "$uuid")
  [ -n "$a" ] && [ -n "$b" ] && [ "$b" -gt "$a" ] 2>/dev/null
}

heal_chain() {
  local name=$1 now; now=$(date +%s)
  local last=${last_heal[$name]:-0}
  [ $((now - last)) -lt "$COOLDOWN_SECONDS" ] && { logger -t guest-leg-watchdog "skip $name — cooldown"; return; }
  heal_count[$name]=$(( ${heal_count[$name]:-0} + 1 ))
  if [ "${heal_count[$name]}" -gt "$GIVE_UP" ]; then
    logger -t guest-leg-watchdog "GIVE UP on $name (${heal_count[$name]}x) — restart won't fix; leaving alone"
    last_heal[$name]=$now; return
  fi
  last_heal[$name]=$now
  logger -t guest-leg-watchdog "HEAL $name public listener (heal #${heal_count[$name]}) — restart fanout only"
  # Restart ONLY the fanout (the public listener). Leave the cores alone so we don't
  # recreate the MXL flows and churn the selector.
  docker restart "${name}-fanout" >/dev/null 2>&1
}

logger -t guest-leg-watchdog "started (ENABLE=$ENABLE, poll ${POLL_SECONDS}s) — watching PUBLIC listeners only; never touches a chain with live video"
while :; do
  IFS=',' read -ra specs <<< "$GUEST_PORTS"
  for spec in "${specs[@]}"; do
    IFS=':' read -r name pub vleg aleg vuuid <<< "$spec"
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${name}-fanout" || continue

    # Only concern: is the PUBLIC listener up? If yes, a contributor can connect → done.
    if bound "$pub"; then strike[$name]=0; heal_count[$name]=0; continue; fi

    # Public listener is DOWN. If the video flow is advancing, a source is live on this
    # slot anyway — do NOT touch it (restarting would churn the selector).
    if video_advancing "$vuuid"; then strike[$name]=0; continue; fi

    # Public down AND no live video = an idle slot whose listener died = a real join block.
    strike[$name]=$(( ${strike[$name]:-0} + 1 ))
    logger -t guest-leg-watchdog "$name public listener :$pub DOWN, no live video (${strike[$name]}/${STRIKES})"
    if [ "${strike[$name]}" -ge "$STRIKES" ]; then
      if [ "$ENABLE" = "1" ]; then heal_chain "$name"; else
        logger -t guest-leg-watchdog "$name would heal, but ENABLE!=1 (observe-only)"; fi
      strike[$name]=0
    fi
  done
  sleep "$POLL_SECONDS"
done
