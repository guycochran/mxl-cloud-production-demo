#!/bin/bash
# program-fps-doctor v2 — detects the repeat-wedged ENCODER reader.
# v1 used mpdecimate (similarity) and would FALSE-TRIGGER on static night
# scenes. v2 counts BYTE-IDENTICAL decoded frames (YDIF==0): x264 emits
# skip-blocks for truly repeated input, while real video always carries
# sensor noise through (median YDIF ~0.16 even on a static console shot).
# Wedged encoder => most frames identical. Healthy static scene => ~0%.
# Cron */5. Cooldown 15 min. Log: /var/log/program-fps-doctor.log
THRESH_PCT=40
COOLDOWN_S=900
LOG=/var/log/program-fps-doctor.log
STAMP=/tmp/.pfps-doctor.last
KEY=5c73394e-85df-50a3-8988-5edde5b5522a
AUD=a0d10000-aaaa-4bbb-8ccc-000000000001

read total zeros < <(timeout 45 ffmpeg -hide_banner \
  -i 'http://127.0.0.1:8888/mxl2webrtc/index.m3u8' -t 10 \
  -vf signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=- -f null - 2>/dev/null \
  | grep -oP 'YDIF=\K[0-9.]+' \
  | awk '{t++; if ($1 < 0.001) z++} END {print t+0, z+0}')
[ -z "$total" ] || [ "$total" -lt 60 ] && { echo "$(date -Is) sample failed (total=${total:-0})" >> "$LOG"; exit 0; }
pct=$((zeros * 100 / total))
if [ "$pct" -lt "$THRESH_PCT" ]; then exit 0; fi

now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((now - last)) -lt "$COOLDOWN_S" ]; then
  echo "$(date -Is) DEGRADED ${pct}% identical but in cooldown" >> "$LOG"; exit 0
fi
echo "$now" > "$STAMP"
echo "$(date -Is) DEGRADED: ${zeros}/${total} frames byte-identical (${pct}%) — surgical encoder restart" >> "$LOG"
curl -s -m 15 -X POST http://127.0.0.1:9601/pipeline/stop -H 'Content-Type: application/json' -d '{}' >/dev/null
sleep 2
curl -s -m 20 -X POST http://127.0.0.1:9601/pipeline/start -H 'Content-Type: application/json' \
  -d "{\"domain_path\":\"/mxl-domain\",\"video_flow_uuid\":\"$KEY\",\"audio_flow_uuid\":\"$AUD\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30,\"intra_refresh\":false}}" >/dev/null
echo "$(date -Is) encoder restarted" >> "$LOG"
