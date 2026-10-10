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
# CPU-spin guard (HW Oct 10): a source that CONNECTS then goes silent (phone stalls,
# Makito unplugged mid-stream) leaves the conform pipeline freewheeling the last frame
# through videorate!videoconvert!v210 at full rate — ~1 full core burned while the flow
# does NOT advance (head frozen). Idle slots that never had a caller sit near 0% (srtsrc
# blocks), so "high CPU + flow not advancing" uniquely identifies the stuck-hot case. We
# restart ONLY the fanout (same safe action as a dead listener) to drop the leg back to
# idle. SPIN_CPU=0 disables this guard; the dead-listener logic below is unaffected.
SPIN_CPU=${SPIN_CPU:-60}            # %CPU above which an ingest counts as "spinning"
SPIN_STRIKES=${SPIN_STRIKES:-3}     # consecutive polls spinning+not-advancing before heal

# name:public:video-leg:audio-leg : video-flow-uuid
GUEST_PORTS=${GUEST_PORTS:-"guest1:8890:8990:9090:9e111e00-aaaa-4bbb-8ccc-000000000001,guest2:8891:8991:9091:9e222e00-aaaa-4bbb-8ccc-000000000001,guest3:8895:8995:9095:9e333e00-aaaa-4bbb-8ccc-000000000001,guest4:8896:8996:9096:9e444e00-aaaa-4bbb-8ccc-000000000001,guest5:8897:8997:9097:9e555e00-aaaa-4bbb-8ccc-000000000001,guest6:8898:8998:9098:9e666e00-aaaa-4bbb-8ccc-000000000001"}

declare -A last_heal heal_count strike spin_strike

bound() { ss -ulnp 2>/dev/null | grep -q ":$1 "; }

# %CPU of the guest's ingest WORKER process (the actual `python3 guest_ingest.py <name>`,
# not the `while :; do ...` supervisor shell that wraps it). Integer; empty if not found.
ingest_cpu() {
  ps -eo %cpu,args 2>/dev/null \
    | grep -E "[p]ython3 guest_ingest\.py $1 " \
    | awk '{c=$1} END{ if (c!="") printf "%d", c }'
}

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

logger -t guest-leg-watchdog "started (ENABLE=$ENABLE, poll ${POLL_SECONDS}s, SPIN_CPU=${SPIN_CPU}%) — watches PUBLIC listeners + CPU-spin; never touches a chain with live (advancing) video"
while :; do
  IFS=',' read -ra specs <<< "$GUEST_PORTS"
  for spec in "${specs[@]}"; do
    IFS=':' read -r name pub vleg aleg vuuid <<< "$spec"
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${name}-fanout" || continue

    # --- CPU-spin guard (runs for EVERY slot, whatever the listener topology) ----------
    # A source can be present yet silent — spinning the conform hot (~1 core) while the
    # flow stays FROZEN. This happens on a bound public listener (Makito unplugged
    # mid-stream) AND on an outbound-pull slot (guest5 phone stalls behind the Azure
    # relay, no local public port), so the check can't live inside the bound-listener
    # branch. "high CPU AND flow not advancing" is the signature; a live source conforms
    # at ~1 core too, so advancing is the discriminator (checked last — it costs a 1s sleep).
    if [ "$SPIN_CPU" -gt 0 ] 2>/dev/null; then
      cpu=$(ingest_cpu "$name")
      if [ -n "$cpu" ] && [ "$cpu" -ge "$SPIN_CPU" ] 2>/dev/null && ! video_advancing "$vuuid"; then
        spin_strike[$name]=$(( ${spin_strike[$name]:-0} + 1 ))
        logger -t guest-leg-watchdog "$name SPINNING (${cpu}% CPU, flow not advancing) (${spin_strike[$name]}/${SPIN_STRIKES})"
        if [ "${spin_strike[$name]}" -ge "$SPIN_STRIKES" ]; then
          if [ "$ENABLE" = "1" ]; then heal_chain "$name"; else
            logger -t guest-leg-watchdog "$name would heal spin, but ENABLE!=1 (observe-only)"; fi
          spin_strike[$name]=0
        fi
        continue   # addressed this slot's health for this poll
      fi
      spin_strike[$name]=0
    fi

    # --- dead-listener guard (idle slot whose PUBLIC listener died = a real join block) --
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
