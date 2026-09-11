#!/usr/bin/env bash
# bring-up-mxl.sh — one-command restore of the full MXL IBC demo
# Restores: Azure VM containers → pipelines (exact live config captured 2026-09-09)
#           → cam push → cloudflared feed tunnel → mxl.html iframe URL → verify.
# Safe to re-run any time (idempotent: stops/starts pipelines, reuses running tunnel).
# Run from the prodbots box:  ~/Projects/bring-up-mxl.sh
set -uo pipefail

VM_IP=20.64.205.144           # Standard SKU public IP = static
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
# test-generator-2 deliberately NOT started — parked 2026-09-10 for CPU headroom
# (cam2 HEVC decode needs the core; backend /api/mxl/status tolerates it being down)
sudo docker start mediamtx test-generator file-player hls2mxl input-selector html5-keyer mxl2webrtc >/dev/null
# mxl2webrtc keyframe patch (2026-09-10): adds POST /pipeline/keyframe so the
# backend can force an IDR on every cut (fixes the mid-GOP smear on switches).
# Files live on the host; container recreation would otherwise lose them.
sudo docker cp /srv/mxl-tools/mxl2webrtc-patch/main.py mxl2webrtc:/app/backend/main.py
sudo docker cp /srv/mxl-tools/mxl2webrtc-patch/gst_mxl2webrtc.py mxl2webrtc:/app/backend/gst_mxl2webrtc.py
sudo docker restart mxl2webrtc >/dev/null
# lower-third graphics for the keyer (bare process, dies on reboot)
pgrep -f "http.server 8085" >/dev/null || \
  (sudo nohup python3 -m http.server 8085 --directory /srv/graphics --bind 0.0.0.0 >/tmp/graphics-8085.log 2>&1 &)
# wait for every backend API
for p in 9600 9601 9602 9603 9604 9605; do  # 9606 = parked TG2, don't wait on it
  for i in $(seq 1 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:$p/pipeline/status && break; sleep 2; done
done
echo "  ✓ containers + :8085 up"
VMEOF

# ── 2. Camera push from this box (must publish before hls2mxl pulls) ─────────
# RESTART (not just start): after a VM reboot/resize the push often connected
# during the VM's boot window before mediamtx was ready, leaving a stale SRT
# handshake — cam1 then never lands and cam_ingest EOS-loops. A restart forces
# a fresh handshake against the now-ready VM. (Sep 11 resize gotcha.)
step "(Re)starting mxl-cam-push (local systemd user service)"
systemctl --user restart mxl-cam-push.service
sleep 4
systemctl --user is-active --quiet mxl-cam-push.service || die "mxl-cam-push failed — journalctl --user -u mxl-cam-push"
echo "  ✓ cam push (re)started"
# Whether SRT is truly landing is proved downstream by the Cam 1 flow existing
# (section 3b) — mediamtx only logs the path on session events, so a steady
# healthy stream shows nothing in a log window (false negative). 3b restarts
# cam-push again if the flow is still missing.

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

# TG2: PARKED 2026-09-10 (CPU headroom for cam2). To revive: docker start
# test-generator-2, re-add 9606 to the wait loop above, and uncomment:
# restart 9606 '{"domain":"/mxl-domain","grouphint":"Test-Generator-2","resolution":"1920x1080","framerate":"30","video":{"active":true,"description":"pinwheel","label":"TG2 Video"},"audio1":{"active":true,"description":"tone A","label":"TG2 Audio 1","channels":2},"audio2":{"active":false,"description":"","label":""},"ancillary":{"active":false,"description":"","label":""}}'
# post 9606/video/test-pattern '{"pattern":"Pinwheel"}'
# post 9606/video/timecode '{"enabled":true}'

# File player: real OHG episode clip, 1080p30 (selector slot 1 "Playout")
restart 9602 '{"domain":"/mxl-domain","file":"ohg-episode30.mp4","grouphint":"File-Player","video":{"active":true,"description":"looping clip 30","label":"Clip Video"},"audio":{"active":true,"description":"clip audio","label":"Clip Audio"}}'

# CAM Live low-latency ingest (replaces the hls2mxl gateway pipeline for the cam):
# rtspsrc latency=150 -> v210 -> PTS re-stamped to now+2 grains -> mxlsink.
# Self-tuning head alignment = camera is an instantly-cuttable selector input.
# (hls2mxl CONTAINER must run for its gst env; its own gateway pipeline stays stopped.)
sudo docker cp /srv/mxl-tools/cam_ingest.py hls2mxl:/tmp/cam_ingest.py
sudo docker exec hls2mxl sh -c 'pkill -f run-cam1.sh; pkill -f cam_ingest.py; pkill -f cam_relay.py; true'
post 9603/pipeline/stop '{}'
# file-based runner (2026-09-10): inline sh-c supervisors carry the kill
# pattern in their own cmdline and die with every pattern-kill of the python
sudo docker exec hls2mxl sh -c 'printf "#!/bin/sh\nwhile :; do python3 /tmp/cam_ingest.py >> /tmp/cam-ingest.log 2>&1; echo RESTART >> /tmp/cam-ingest.log; sleep 2; done\n" > /tmp/run-cam1.sh && chmod +x /tmp/run-cam1.sh'
sudo docker exec -d hls2mxl /tmp/run-cam1.sh
sleep 6

# CAM 2 Live ingest (Makito X4 static SDI cam -> SRT publish:cam2 -> mediamtx).
# The Makito (192.168.8.177, encoder 1 + stream "MXL Cam2 SRT", saved to startup
# preset) calls in on its own — nothing to start VM-side for the feed itself.
# Makito encoder 1 = H.264 High 4:2:0 12 Mbps, SRT latency 20ms (2026-09-10, RTT~6ms) (switched from HEVC 2026-09-10:
# HEVC software-decode was ~130% of a core and starved the keyer; H.264 ~50%).
# cam2_ingest.py uses avdec_h264 thread-type=frame; Makito must stay slices=1
# (slices=4 broke mediamtx's TS parsing). Runner keeps cmdline pkill-safe.
sudo docker cp /srv/mxl-tools/cam2_ingest.py hls2mxl:/tmp/cam2_ingest.py
sudo docker exec hls2mxl sh -c 'pkill -f run-cam2.sh; pkill -f cam2_ingest.py; true'
sudo docker exec hls2mxl sh -c 'printf "#!/bin/sh\nwhile :; do nice -n 10 python3 /tmp/cam2_ingest.py >> /tmp/cam2-ingest.log 2>&1; echo RESTART >> /tmp/cam2-ingest.log; sleep 2; done\n" > /tmp/run-cam2.sh && chmod +x /tmp/run-cam2.sh'
sudo docker exec -d hls2mxl /tmp/run-cam2.sh
sleep 6

# PGM Audio: RESTORED 2026-09-10 evening (VM1 resized to D16s_v5 — the two
# mxlsrc audio readers busy-spin ~2 cores, affordable on 16, NOT on 8).
# audio_pgm.py = audio-follow-video: playout=episode, pattern=tone, else silence.
# If the VM ever shrinks back to 8 cores: kill this and drop audio_flow_uuid
# from the encoder body below (see audio_pgm.py.full history).
sudo docker cp /srv/mxl-tools/audio_pgm.py hls2mxl:/tmp/audio_pgm.py
sudo docker exec hls2mxl sh -c 'pkill -9 -f run-audio.sh; pkill -9 -f audio_pgm.py; true'
sudo docker exec hls2mxl sh -c 'printf "#!/bin/sh\nwhile :; do nice -n 5 python3 /tmp/audio_pgm.py >> /tmp/audio-pgm.log 2>&1; echo RESTART >> /tmp/audio-pgm.log; sleep 2; done\n" > /tmp/run-audio.sh && chmod +x /tmp/run-audio.sh'
sudo docker exec -d hls2mxl /tmp/run-audio.sh
# Guest AUDIO writers (2026-09-11): pull contributor audio from VM2 mediamtx
# over the VNet -> "Guest N Audio" flows; audio_pgm v2 (audiomixer) adopts
# them on appearance (rebuild + automatic encoder re-attach via backend).
sudo docker cp /srv/mxl-tools/guest_audio.py hls2mxl:/tmp/guest_audio.py
sudo docker exec hls2mxl sh -c 'pkill -9 -f run-gaudio; pkill -9 -f guest_audio.py; true'
for n in 1 2; do
  sudo docker exec hls2mxl sh -c "printf '#!/bin/sh\nwhile :; do nice -n 6 python3 /tmp/guest_audio.py guest$n a$n$n$n${n}e00-aaaa-4bbb-8ccc-000000000001 \"Guest $n Audio\" >> /tmp/guest$n-audio.log 2>&1; sleep 3; done\n' > /tmp/run-gaudio$n.sh && chmod +x /tmp/run-gaudio$n.sh"
  sudo docker exec -d hls2mxl /tmp/run-gaudio$n.sh
done
sleep 4

# Multiview thumbnails (added 2026-09-10): mxl_thumbs.py writes 320x180 JPEGs
# per input into /mxl-domain/thumbs; host serves them on 127.0.0.1:8086
# (symlinked under /srv/thumbs-www) -> mxl-feed tunnel /thumbs/* path ->
# backend /api/mxl/thumbs proxy -> PVW-row thumbnails on mxl.html.
sudo docker cp /srv/mxl-tools/mxl_thumbs.py hls2mxl:/tmp/mxl_thumbs.py
sudo docker exec hls2mxl sh -c 'pkill -9 -f run-thumbs.sh; pkill -9 -f mxl_thumbs.py; true'
sudo docker exec hls2mxl sh -c 'printf "#!/bin/sh\nwhile :; do nice -n 15 python3 /tmp/mxl_thumbs.py >> /tmp/mxl-thumbs.log 2>&1; echo RESTART >> /tmp/mxl-thumbs.log; sleep 3; done\n" > /tmp/run-thumbs.sh && chmod +x /tmp/run-thumbs.sh'
sudo docker exec -d hls2mxl /tmp/run-thumbs.sh
sudo mkdir -p /srv/thumbs-www && sudo ln -sfn /dev/shm/mxl/domain_1/thumbs /srv/thumbs-www/thumbs
pgrep -f "http.server 8086" >/dev/null || \
  sudo bash -c 'nohup python3 -m http.server 8086 --directory /srv/thumbs-www --bind 127.0.0.1 >/tmp/thumbs-8086.log 2>&1 &'

# Layout compositor (2-up / PiP, selector slot 6, added 2026-09-10): always-on,
# all six sources behind two internal selectors -> compositor; layout changes
# are live pad-property flips (no flow recreation -> no slot-6 wedges). Polls
# prodbots.com/api/mxl/layout-state for desired state (browser-UA header —
# the CF zone 403s python-urllib).
sudo docker cp /srv/mxl-tools/layout_pgm.py hls2mxl:/tmp/layout_pgm.py
sudo docker exec hls2mxl sh -c 'pkill -9 -f run-layout.sh; pkill -9 -f layout_pgm.py; true'
sudo docker exec hls2mxl sh -c 'printf "#!/bin/sh\nwhile :; do nice -n 8 python3 /tmp/layout_pgm.py >> /tmp/layout-pgm.log 2>&1; echo RESTART >> /tmp/layout-pgm.log; sleep 3; done\n" > /tmp/run-layout.sh && chmod +x /tmp/run-layout.sh'
sudo docker exec -d hls2mxl /tmp/run-layout.sh
sleep 5

# Selector: 0=CAM 1=Playout 2=TG 3=CAM2 4=guest1 5=guest2 6=Layout (UUIDs deterministic).
# Container MUST have been created with -e MAX_INPUTS=7 (recreated 2026-09-10; if
# it is ever recreated from scratch, keep that env or slot 6 is rejected).
# NOTE: this 4-input body is the SAFE BASE (absent guests would 400 the start);
# the backend repair (/api/mxl/repair) immediately supersedes it with the full
# 7-input set minus whatever flows are absent — ALWAYS run a repair after bring-up.
restart 9604 '{"domain_path":"/mxl-domain","input_flow_uuids":["ca111e00-aaaa-4bbb-8ccc-000000000001","2f34c189-64bf-5971-993a-332a28a7a6ee","6b5d8d68-64ce-56f8-bea2-e79b6c282a86","ca222e00-aaaa-4bbb-8ccc-000000000001"],"grouphint":"Input-Selector","description":"program out","label":"Selector PGM"}'
post 9604/pipeline/active-input '{"slot":0}'

# Keyer: OHG lower-third keyed over SELECTOR PGM (key stays up across all cuts)
restart 9605 '{"mode":"key","domain_path":"/mxl-domain","input_flow_uuid":"9437652d-20d9-565e-be6e-b98c36067930","html5_url":"http://host.docker.internal:8085/lower-third.html?v=nodip1","grouphint":"HTML5-Keyer","description":"cam + graphics","label":"Keyer PGM"}'
post 9605/pipeline/key '{"on":true}'
sleep 3

# WebRTC encoder: Keyer PGM video + PGM Audio (audio-follow-video), via mediamtx
restart 9601 '{"domain_path":"/mxl-domain","video_flow_uuid":"5c73394e-85df-50a3-8988-5edde5b5522a","audio_flow_uuid":"a0d10000-aaaa-4bbb-8ccc-000000000001","use_mediamtx":true,"encoder":{"tune":4,"speed_preset":2,"bitrate":6000,"key_int_max":30,"intra_refresh":false}}'
echo "  ✓ pipelines restarted"
VMEOF

# ── 3b. Ensure exactly one of each single-writer pipeline ─────────────────────
# The pre-existing "Restarting pipelines" section restarts the docker-exec
# supervisors, but its `pkill -f run-camN.sh` self-matches (the killing shell's
# own cmdline contains the pattern → it kills itself before finishing), which on
# an already-running box leaves ORPHANED children + DUPLICATE supervisors — two
# writers fight one flow and it never attaches. ensure-single-writers.sh uses
# [x]-bracket patterns (immune to self-match) to force exactly one, and --start
# launches any that are at zero. Idempotent — safe on a healthy box too.
step "Cam-push fresh handshake if Cam 1 flow is absent"
if ! $SSH 'sudo docker exec hls2mxl sh -c "ls /mxl-domain/ 2>/dev/null | grep -qi ca111e00"'; then
  echo "  … Cam 1 flow absent — re-kicking local cam-push (SRT handshake often stale after reboot)"
  systemctl --user restart mxl-cam-push.service; sleep 6
fi
step "Ensuring exactly one of each pipeline writer"
scp -q -i "$SSH_KEY" "$HOME/Projects/ensure-single-writers.sh" guy@$VM_IP:/tmp/ensure-single-writers.sh
$SSH 'bash /tmp/ensure-single-writers.sh --start'
sleep 12
# Report which source flows actually exist now
$SSH 'for u in ca111e00:Cam1 ca222e00:Cam2 1a900700:Layout; do uuid=${u%%:*}; nm=${u##*:}; sudo docker exec hls2mxl sh -c "ls /mxl-domain/ 2>/dev/null | grep -qi $uuid" && echo "  ✓ $nm flow present" || echo "  ⚠ $nm flow MISSING — check source"; done'

# ── 3c. Fresh layout output lock ──────────────────────────────────────────────
# A layout_pgm that started during API-flaky recovery can lock its output servo
# ~1s off and hard-re-lock every frame forever (branches freeze-then-jump on
# motion). v7 auto-heals this, but a clean respawn here guarantees a good lock
# from the start. Safe: program is cut to cam below, so layout is off-air.
step "Fresh layout output lock"
$SSH 'sudo docker exec hls2mxl sh -c "pkill -9 -f \"[r]un-layout\"; pkill -9 -f \"[l]ayout_pgm.py\"; sleep 2"; sudo docker exec -d hls2mxl /tmp/run-layout.sh; echo "  ✓ layout respawned"'

# ── 4. Feed tunnel (named: mxl-feed.cochran.cloud, systemd on the VM) ────────
# Tunnel UUID e5ec74db-8e99-4232-b5a4-4e240f88572b; config /etc/cloudflared/mxl-feed.yml.
# systemd auto-starts it on boot — this just makes sure and verifies.
step "Ensuring mxl-feed named tunnel on VM"
$SSH 'sudo systemctl start mxl-feed-tunnel.service; systemctl is-active mxl-feed-tunnel.service' | tail -1
FEED_URL=https://mxl-feed.cochran.cloud
echo "  ✓ feed tunnel: $FEED_URL (stable — mxl.html needs no rewriting)"

# ── 5. Repair cascade + cut to Cam 1 ─────────────────────────────────────────
# Repair supersedes the 4-input safe-base selector with the full set minus any
# absent flows, and re-attaches slot 6 (layout was just respawned). Give the
# freshly respawned writers a moment to create their flows first.
step "Repair cascade + program to Cam 1"
sleep 8
curl -s -m 30 -X POST -H 'Content-Type: application/json' -d '{}' https://prodbots.com/api/mxl/repair >/dev/null 2>&1 || true
sleep 3
curl -s -m 15 -X POST -H 'Content-Type: application/json' -d '{"input":0}' https://prodbots.com/api/mxl/input >/dev/null 2>&1 || true
echo "  ✓ repaired + on Cam 1"

# ── 6. Verify ────────────────────────────────────────────────────────────────
step "Verifying"
sleep 5
c1=$(curl -s -o /dev/null -w '%{http_code}' https://prodbots.com/mxl.html)
c2=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$FEED_URL/mxl2webrtc/")
# Health endpoint = source of truth for slot liveness + writer census.
hs=$(curl -s -m 10 https://prodbots.com/api/mxl/status | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except: print("status unreachable"); raise SystemExit
live=[s["name"] for s in d["slots"] if s["live"]]; down=[s["name"] for s in d["slots"] if not s["live"]]
print(f"pgm=slot{d[\"input\"]} live={\",\".join(live)} down={\",\".join(down) or \"none\"}")' 2>/dev/null)
echo "  mxl.html: $c1 | feed: $c2"
echo "  $hs"
# Duplicate-writer guard — the exact failure the Sep 11 resize hit.
dups=$(curl -s -m 10 https://prodbots.com/api/mxl/health | python3 -c 'import json,sys
try: h=json.load(sys.stdin)
except: raise SystemExit
d=[f"{k}x{v}" for k,v in (h.get("procs") or {}).items() if v>1]
print("DUPLICATE WRITERS: "+",".join(d) if d else "writers: all single")' 2>/dev/null)
echo "  $dups"
echo
echo "✅ DEMO: https://prodbots.com/mxl.html"
echo "   Teardown: pkill cloudflared on VM; systemctl --user stop mxl-cam-push;"
echo "             az vm deallocate -g ohg-mxl-lab -n mxl-lab"
