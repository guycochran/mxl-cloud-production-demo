#!/bin/bash
# guest-leg-doctor — fast (15s) healer for the guest fabric legs.
# Every contributor reconnect (cellular EOS, Larix stop/start) recreates the
# VM2 source flow, which wedges the leg on BOTH ends: the initiator's reader
# repeats/misses grains, and the VM1 target can freeze its slice stream while
# still counting grains. Heal order matters: restart the VM1 TARGET first
# (rewrites rkeys), then the local initiator (ExecStartPre fetches them).
# Replaces the 2-min guest-initiator-watchdog cron (too slow for phone churn).
TOKEN=$(grep -oP '^EASY_MXL_TOKEN=\K.*' /etc/default/easy-mxl)
SSH_VM1="ssh -i /home/guy/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/guy/.ssh/known_hosts guy@10.0.0.4"
declare -A cooldown
PGM_BODY='{"domain_path":"/mxl-domain","video_flow_uuid":"373517cc-9e60-446a-af59-c115240edbc0","use_mediamtx":true,"encoder":{"tune":4,"speed_preset":2,"bitrate":6000,"key_int_max":30,"intra_refresh":false}}'
heal_pgm(){
  logger -t guest-leg-doctor "healing PGM leg (target log stale) — target, rkeys, VM1 initiator, viewer"
  pid=$(pgrep -f "domain_fabric.*-s 1313" | head -1)
  [ -n "$pid" ] && kill -9 "$pid"
  sleep 1
  sudo -u guy bash -c 'nohup /home/guy/mxl/build/Linux-GCC-Release/tools/mxl-fabrics-demo/mxl-fabrics-demo -d /dev/shm/mxl/domain_fabric -p tcp -n 10.0.0.5 -s 1313 -f /home/guy/fabric/pgm-flow.json -t @/home/guy/fabric/pgm-target.json >> /home/guy/fabric/pgm-target.log 2>&1 &'
  sleep 3
  scp -i /home/guy/.ssh/id_ed25519 -o BatchMode=yes -o UserKnownHostsFile=/home/guy/.ssh/known_hosts /home/guy/fabric/pgm-target.json guy@10.0.0.4:/home/guy/fabric/pgm-target.json
  $SSH_VM1 "sudo systemctl restart mxl-pgm-initiator"
  docker restart mxl2webrtc >/dev/null
  sleep 8
  curl -s -m 20 -X POST -H "Content-Type: application/json" -d "$PGM_BODY" http://127.0.0.1:9601/pipeline/start >/dev/null
  logger -t guest-leg-doctor "PGM leg heal complete"
}
while true; do
  sleep 15
  # PGM leg: a healthy target logs stats every ~2s; a silent log means the
  # frozen/degraded target state that fed the TAMS recorder 9fps for 11 hours
  if [ -f /home/guy/fabric/pgm-target.log ]; then
    age=$(( $(date +%s) - $(stat -c %Y /home/guy/fabric/pgm-target.log) ))
    # frozen-slices has a STEALTH variant: log stays live (grains tick 30/s)
    # while the slice counter freezes — avg slices/grain decays from 1080.
    # Healthy is ALWAYS "avg 1080.0"; anything under 1000 is sick.
    pavg=$(tail -1 /home/guy/fabric/pgm-target.log | grep -oP 'avg \K[0-9]+' | head -1)
    now=$(date +%s); last=${cooldown[pgm]:-0}
    if { [ "$age" -gt 120 ] || { [ -n "$pavg" ] && [ "$pavg" -lt 1000 ]; }; } && [ $((now - last)) -gt 600 ]; then
      cooldown[pgm]=$now
      logger -t guest-leg-doctor "PGM sick (age=${age}s avg=${pavg:-?})"
      heal_pgm
    fi
  fi
  load=$(awk '{print $1}' /proc/loadavg)
  [ "${load%%.*}" -ge 16 ] && continue
  flows=$(curl -s -m 6 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9700/api/domains/domain_1/flows)
  for leg in guest1 guest2; do
    [ "$leg" = guest1 ] && u=9e111e00 || u=9e222e00
    systemctl is-active --quiet mxl-$leg-initiator || continue
    age=$(echo "$flows" | python3 -c "
import json,sys,datetime
now=datetime.datetime.now(datetime.timezone.utc)
for f in json.load(sys.stdin):
    if f.get('id','').startswith('$u'):
        h=f.get('headTimeIso')
        if h: print(int((now-datetime.datetime.fromisoformat(h.replace('Z','+00:00'))).total_seconds()))
" 2>/dev/null)
    [ -n "$age" ] && [ "$age" -lt 10 ] || continue   # no local writer -> idle leg
    bad=$(tail -n 12 /home/guy/fabric/$leg-initiator.log 2>/dev/null | grep -cE "Missed grain|No more targets")
    # target-side frozen-slices state: grains tick at 30/s but the slice
    # payload counter stalls -> avg slices/grain decays from 1080 toward 0
    # while the INITIATOR looks perfectly healthy (tonight's silent killer)
    tavg=$($SSH_VM1 "sudo tail -1 /home/guy/fabric/$leg-target.log 2>/dev/null" | grep -oP 'avg \K[0-9]+' | head -1)
    tsick=0
    [ -n "$tavg" ] && [ "$tavg" -lt 500 ] && tsick=1
    { [ "$bad" -ge 12 ] || [ "$tsick" = 1 ]; } || continue
    now=$(date +%s); last=${cooldown[$leg]:-0}
    [ $((now - last)) -lt 40 ] && continue
    cooldown[$leg]=$now
    logger -t guest-leg-doctor "healing $leg (writer fresh ${age}s, initiator-bad=$bad target-avg=${tavg:-?}) — target then initiator"
    tgt=mxl-$leg-target
    $SSH_VM1 "sudo systemctl restart $tgt" && sleep 3
    systemctl restart mxl-$leg-initiator
  done
done
