#!/usr/bin/env bash
# bring-up-mxl.sh — one-command restore of the full MXL IBC demo
# Restores: Azure VM containers → pipelines (exact live config captured 2026-09-09)
#           → cam push → cloudflared feed tunnel → mxl.html iframe URL → verify.
# Safe to re-run any time (idempotent: stops/starts pipelines, reuses running tunnel).
# Run from the prodbots box:  ~/Projects/bring-up-mxl.sh
set -uo pipefail

VM_IP=YOUR_VM_IP           # Standard SKU public IP = static
SSH_KEY=$HOME/.ssh/mxl-lab
SSH="ssh -i $SSH_KEY -o BatchMode=yes -o ConnectTimeout=8 guy@$VM_IP"
MXL_HTML=$HOME/prodbots-backend/public/mxl.html

step() { echo; echo "▶ $*"; }
die()  { echo "✗ $*" >&2; exit 1; }

# ── 0. VM reachable? (start it if az is available) ───────────────────────────
step "Checking VM $VM_IP"
if ! $SSH true 2>/dev/null; then
  echo "  VM unreachable — trying az vm start (ok if az not logged in)…"
  az vm start -g ohg-mxl-lab -n mxl-lab 2>/dev/null \
    || die "VM down and az start failed. Login first: az login --service-principal (SP mxl-lab-cli), then re-run."
  for i in $(seq 1 30); do $SSH true 2>/dev/null && break; sleep 10; done
  $SSH true 2>/dev/null || die "VM started but SSH still unreachable (NSG allows only site IP 50.106.4.50/32 — did the site IP change?)"
fi
echo "  ✓ SSH ok"

# ── 1. Containers + graphics server on the VM ────────────────────────────────
step "Starting containers + graphics server on VM"
$SSH 'bash -s' <<'VMEOF'
set -u
sudo docker start mediamtx test-generator test-generator-2 file-player hls2mxl input-selector html5-keyer mxl2webrtc >/dev/null
# lower-third graphics for the keyer (bare process, dies on reboot)
pgrep -f "http.server 8085" >/dev/null || \
  (sudo nohup python3 -m http.server 8085 --directory /srv/graphics --bind 0.0.0.0 >/tmp/graphics-8085.log 2>&1 &)
# wait for every backend API
for p in 9600 9601 9602 9603 9604 9605 9606; do
  for i in $(seq 1 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:$p/pipeline/status && break; sleep 2; done
done
echo "  ✓ containers + :8085 up"
VMEOF

# ── 2. Camera push from this box (must publish before hls2mxl pulls) ─────────
step "Starting mxl-cam-push (local systemd user service)"
systemctl --user start mxl-cam-push.service
sleep 3
systemctl --user is-active --quiet mxl-cam-push.service || die "mxl-cam-push failed — journalctl --user -u mxl-cam-push"
echo "  ✓ cam push active"

# ── 3. Pipelines, dependency order (writers → selector/keyer → encoder) ──────
# Labels/descriptions/grouphints MUST stay byte-identical: flow UUIDs are
# deterministic from them, and selector/keyer/encoder reference those UUIDs.
step "Restarting pipelines on VM (containers boot stateless)"
$SSH 'bash -s' <<'VMEOF'
set -u
post() { curl -s -m 20 -X POST -H "Content-Type: application/json" -d "$2" "http://127.0.0.1:$1" >/dev/null; }
restart() { post "$1/pipeline/stop" '{}'; sleep 1; post "$1/pipeline/start" "$2"; }

# TG1: bars (selector slot 0)
restart 9600 '{"domain":"/mxl-domain","grouphint":"Test-Generator","resolution":"1920x1080","framerate":"30","video":{"active":true,"description":"OHG lab test video","label":"TG Video"},"audio1":{"active":true,"description":"tone 1","label":"TG Audio 1","channels":2},"audio2":{"active":true,"description":"tone 2","label":"TG Audio 2","channels":2},"ancillary":{"active":false,"description":"","label":""}}'
post 9600/video/test-pattern '{"pattern":"100% bars"}'

# TG2: pinwheel + timecode (selector slot 2)
restart 9606 '{"domain":"/mxl-domain","grouphint":"Test-Generator-2","resolution":"1920x1080","framerate":"30","video":{"active":true,"description":"pinwheel","label":"TG2 Video"},"audio1":{"active":true,"description":"tone A","label":"TG2 Audio 1","channels":2},"audio2":{"active":false,"description":"","label":""},"ancillary":{"active":false,"description":"","label":""}}'
post 9606/video/test-pattern '{"pattern":"Pinwheel"}'
post 9606/video/timecode '{"enabled":true}'

# File player: real OHG episode clip, 1080p30 (selector slot 1 "Playout")
restart 9602 '{"domain":"/mxl-domain","file":"ohg-episode30.mp4","grouphint":"File-Player","video":{"active":true,"description":"looping clip 30","label":"Clip Video"},"audio":{"active":true,"description":"clip audio","label":"Clip Audio"}}'

# CAM Live low-latency ingest (replaces the hls2mxl gateway pipeline for the cam):
# rtspsrc latency=150 -> v210 -> PTS re-stamped to now+2 grains -> mxlsink.
# Self-tuning head alignment = camera is an instantly-cuttable selector input.
# (hls2mxl CONTAINER must run for its gst env; its own gateway pipeline stays stopped.)
sudo docker cp /srv/mxl-tools/cam_ingest.py hls2mxl:/tmp/cam_ingest.py
sudo docker exec hls2mxl sh -c 'pkill -f cam_ingest.py; pkill -f cam_relay.py; true'
post 9603/pipeline/stop '{}'
sudo docker exec -d hls2mxl sh -c 'while :; do python3 /tmp/cam_ingest.py >> /tmp/cam-ingest.log 2>&1; echo RESTART >> /tmp/cam-ingest.log; sleep 2; done'
sleep 6

# PGM Audio: audio-follow-video switcher (silence/episode/tone tracks the selector)
sudo docker cp /srv/mxl-tools/audio_pgm.py hls2mxl:/tmp/audio_pgm.py
sudo docker exec hls2mxl sh -c 'pkill -f audio_pgm.py; true'
sudo docker exec -d hls2mxl sh -c 'while :; do python3 /tmp/audio_pgm.py >> /tmp/audio-pgm.log 2>&1; echo RESTART >> /tmp/audio-pgm.log; sleep 2; done'
sleep 3

# Selector: 0=CAM Live 1=Playout clip 2=bars TG (UUIDs deterministic)
restart 9604 '{"domain_path":"/mxl-domain","input_flow_uuids":["ca111e00-aaaa-4bbb-8ccc-000000000001","2f34c189-64bf-5971-993a-332a28a7a6ee","6b5d8d68-64ce-56f8-bea2-e79b6c282a86"],"grouphint":"Input-Selector","description":"program out","label":"Selector PGM"}'
post 9604/pipeline/active-input '{"slot":0}'

# Keyer: OHG lower-third keyed over SELECTOR PGM (key stays up across all cuts)
restart 9605 '{"mode":"key","domain_path":"/mxl-domain","input_flow_uuid":"9437652d-20d9-565e-be6e-b98c36067930","html5_url":"http://host.docker.internal:8085/lower-third.html","grouphint":"HTML5-Keyer","description":"cam + graphics","label":"Keyer PGM"}'
post 9605/pipeline/key '{"on":true}'
sleep 3

# WebRTC encoder: Keyer PGM video + TG audio, via mediamtx
restart 9601 '{"domain_path":"/mxl-domain","video_flow_uuid":"5c73394e-85df-50a3-8988-5edde5b5522a","audio_flow_uuid":"a0d10000-aaaa-4bbb-8ccc-000000000001","use_mediamtx":true,"encoder":{"tune":4,"speed_preset":2,"bitrate":6000,"key_int_max":30,"intra_refresh":false}}'
echo "  ✓ pipelines restarted"
VMEOF

# ── 4. Feed tunnel (ephemeral trycloudflare URL) ─────────────────────────────
step "Ensuring cloudflared feed tunnel on VM"
FEED_URL=$($SSH 'pgrep -f "cloudflared tunnel --url http://127.0.0.1:8889" >/dev/null || \
    (nohup cloudflared tunnel --url http://127.0.0.1:8889 >/tmp/mxl-mtx-tunnel.log 2>&1 & sleep 8); \
  grep -ho "https://[a-z0-9-]*\.trycloudflare\.com" /tmp/mxl-mtx-tunnel.log 2>/dev/null | tail -1')
[ -n "$FEED_URL" ] || die "Could not determine tunnel URL — check /tmp/mxl-mtx-tunnel.log on the VM"
echo "  ✓ feed tunnel: $FEED_URL"

# ── 5. Point mxl.html at the current tunnel URL ──────────────────────────────
step "Updating mxl.html iframe (static file, no backend restart)"
if grep -q "$FEED_URL" "$MXL_HTML"; then
  echo "  ✓ already current"
else
  sed -i.bak-bringup -E "s#https://[a-z0-9-]+\.trycloudflare\.com#$FEED_URL#g" "$MXL_HTML"
  echo "  ✓ rewrote tunnel URL → $FEED_URL (backup: mxl.html.bak-bringup)"
fi

# ── 6. Verify ────────────────────────────────────────────────────────────────
step "Verifying"
sleep 5
c1=$(curl -s -o /dev/null -w '%{http_code}' https://your-site.example/mxl.html)
c2=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$FEED_URL/mxl2webrtc/")
cam=$($SSH 'curl -s http://127.0.0.1:9603/pipeline/status' | grep -o '"running":true' || true)
echo "  mxl.html: $c1 | feed: $c2 | cam gateway running: ${cam:-NO}"
$SSH 'curl -s -H "Authorization: Bearer YOUR_EASY_MXL_TOKEN" http://127.0.0.1:9700/api/domains/domain_1/flows' \
  | python3 -c 'import json,sys; [print(f"  {f[\"label\"]:26} active={f[\"active\"]}") for f in json.load(sys.stdin)]' 2>/dev/null
echo
echo "✅ DEMO: https://your-site.example/mxl.html"
echo "   Teardown: pkill cloudflared on VM; systemctl --user stop mxl-cam-push;"
echo "             az vm deallocate -g ohg-mxl-lab -n mxl-lab"
