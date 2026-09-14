#!/bin/bash
# program-fps-doctor — catches the repeat-wedged ENCODER reader species:
# program shows ~9fps of unique frames while every rate/head/thumb check
# is green (bit Guy 9/12 morning, 9/12 evening, 9/14). Measures UNIQUE fps
# at the encoder's own HLS output with mpdecimate; below threshold ->
# surgical encoder-only pipeline restart (selector/keyer untouched).
# Cron: */5 min. Cooldown 15 min. Log: /var/log/program-fps-doctor.log
THRESH=20          # unique fps; clean is 28-30, wedged is ~9
COOLDOWN_S=900
LOG=/var/log/program-fps-doctor.log
STAMP=/tmp/.pfps-doctor.last
KEY=5c73394e-85df-50a3-8988-5edde5b5522a
AUD=a0d10000-aaaa-4bbb-8ccc-000000000001

frames=$(timeout 40 ffmpeg -hide_banner -loglevel info \
  -i 'http://127.0.0.1:8888/mxl2webrtc/index.m3u8' -t 10 -vf mpdecimate -f null - 2>&1 \
  | grep -oP 'frame=\s*\K[0-9]+' | tail -1)
[ -z "$frames" ] && { echo "$(date -Is) sample failed (stream down?)" >> "$LOG"; exit 0; }
ufps=$((frames / 10))
if [ "$ufps" -ge "$THRESH" ]; then exit 0; fi

now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ $((now - last)) -lt "$COOLDOWN_S" ]; then
  echo "$(date -Is) DEGRADED ufps=$ufps but in cooldown" >> "$LOG"; exit 0
fi
echo "$now" > "$STAMP"
echo "$(date -Is) DEGRADED ufps=$ufps (<$THRESH) — surgical encoder restart" >> "$LOG"
curl -s -m 15 -X POST http://127.0.0.1:9601/pipeline/stop -H 'Content-Type: application/json' -d '{}' >/dev/null
sleep 2
curl -s -m 20 -X POST http://127.0.0.1:9601/pipeline/start -H 'Content-Type: application/json' \
  -d "{\"domain_path\":\"/mxl-domain\",\"video_flow_uuid\":\"$KEY\",\"audio_flow_uuid\":\"$AUD\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30,\"intra_refresh\":false}}" >/dev/null
echo "$(date -Is) encoder restarted" >> "$LOG"
