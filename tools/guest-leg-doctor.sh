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
while true; do
  sleep 15
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
