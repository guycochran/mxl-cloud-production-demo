#!/usr/bin/env bash
# quickstart.sh — fresh Ubuntu VM → cuttable MXL switcher in one command.
#
#   curl -fsSL https://raw.githubusercontent.com/guycochran/mxl-cloud-production-demo/master/scripts/quickstart.sh | sudo bash
#   (or clone the repo and: sudo scripts/quickstart.sh)
#
# What you get: an EBU MXL shared-memory domain with a test generator, a file
# player (sample clip auto-downloaded), a 7-slot input selector, an HTML5
# graphics keyer (lower-third + clock), and a WebRTC encoder — all stock
# ghcr.io/cbcrc containers — plus the curl one-liners to CUT between sources
# and a browser URL to watch the program. No accounts, no external services.
#
# Requirements: Ubuntu 22.04/24.04, x86-64 CPU **with AVX** (any Azure
# D-series v5, AWS m5/m6i, GCP n2; QEMU needs `-cpu host` — see FINDINGS §11),
# ~8 vCPU recommended, ports open in your cloud firewall:
#   8889/tcp (WebRTC page+signaling)  8189/udp (WebRTC media)
#   8890/udp (optional: SRT contribution for your own camera)
#
# Idempotent: safe to re-run. Teardown: sudo scripts/quickstart.sh --down
set -euo pipefail

DOMAIN_HOST=/dev/shm/mxl/domain_1          # shared-memory domain (volatile: gone on reboot — just re-run)
BASE=/srv/mxl-quickstart                   # clips + graphics live here (persistent)
IMAGES="bluenviron/mediamtx:latest ghcr.io/cbcrc/test-generator:latest ghcr.io/cbcrc/file-player:latest ghcr.io/cbcrc/input-selector:latest ghcr.io/cbcrc/html5-keyer:latest ghcr.io/cbcrc/mxl2webrtc:latest"
CONTAINERS="mediamtx test-generator file-player input-selector html5-keyer mxl2webrtc"
# official Blender mirror, natively 1080p30 (the Google sample bucket 403s now)
CLIP_URL="https://download.blender.org/demo/movies/BBB/bbb_sunflower_1080p_30fps_normal.mp4"

step(){ echo; echo "▶ $*"; }
die(){ echo "✗ $*" >&2; exit 1; }
post(){ curl -s -m 25 -X POST -H 'Content-Type: application/json' -d "$2" "http://127.0.0.1:$1" ; }

# ── teardown ──────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--down" ]; then
  step "Tearing down quickstart containers + graphics server"
  docker rm -f $CONTAINERS 2>/dev/null || true
  pkill -f "http.server 8085" 2>/dev/null || true
  echo "Done. ($BASE and the domain dir are left in place; rm -rf $BASE to remove.)"
  exit 0
fi

# ── 0. preflight ──────────────────────────────────────────────────────────────
step "Preflight"
[ "$(id -u)" = 0 ] || die "run with sudo (docker + /srv access)"
grep -qm1 avx /proc/cpuinfo || die "CPU has no AVX — libmxl will SIGILL. On QEMU/KVM set the CPU model to 'host'. (FINDINGS §11)"
command -v docker >/dev/null || { echo "  installing docker…"; curl -fsSL https://get.docker.com | sh >/dev/null; }
command -v python3 >/dev/null || apt-get install -y -qq python3 >/dev/null
PUBLIC_IP=$(curl -s -m 8 https://ifconfig.me || true)
[ -n "$PUBLIC_IP" ] || PUBLIC_IP=$(hostname -I | awk '{print $1}')
echo "  ✓ AVX ok · docker ok · public IP: $PUBLIC_IP"

# ── 1. dirs, sample clip, lower-third graphic ────────────────────────────────
step "Media + graphics"
mkdir -p "$DOMAIN_HOST" "$BASE/clips" "$BASE/graphics"
chmod 777 "$DOMAIN_HOST"   # every container writes flows here
# Clip strategy (domain runs 30/1, so 1080p30 cuts cleanest):
#   1. a file YOU dropped at clips/sample.mp4 wins (conformed if ffmpeg exists)
#   2. else download Big Buck Bunny 1080p30 from the official Blender mirror
#   3. else GENERATE a moving test clip with ffmpeg — no network needed, the
#      script can't die on a dead sample-URL again (Google's bucket 403'd 9/13)
if [ -s "$BASE/clips/sample.mp4" ] && command -v ffmpeg >/dev/null && [ ! -s "$BASE/clips/sample-1080p30.mp4" ]; then
  echo "  conforming your clip to 1080p30 (one-time)…"
  ffmpeg -loglevel error -y -i "$BASE/clips/sample.mp4" -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30" \
    -c:v libx264 -preset fast -crf 20 -c:a aac -t 120 "$BASE/clips/sample-1080p30.mp4" || true
fi
if [ ! -s "$BASE/clips/sample-1080p30.mp4" ] && [ ! -s "$BASE/clips/sample.mp4" ]; then
  echo "  downloading sample clip (Big Buck Bunny 1080p30, ~265 MB)…"
  curl -fSL --progress-bar -o "$BASE/clips/sample-1080p30.mp4" "$CLIP_URL" || {
    echo "  download failed — generating a local test clip instead (ffmpeg)…"
    rm -f "$BASE/clips/sample-1080p30.mp4"
    command -v ffmpeg >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null
    ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=1920x1080:rate=30" -f lavfi -i "sine=frequency=440:sample_rate=48000" \
      -t 90 -c:v libx264 -preset fast -pix_fmt yuv420p -c:a aac "$BASE/clips/sample-1080p30.mp4" \
      || die "could not download OR generate a clip — put any H.264 mp4 at $BASE/clips/sample.mp4 and re-run"
  }
fi
CLIP=sample.mp4; [ -s "$BASE/clips/sample-1080p30.mp4" ] && CLIP=sample-1080p30.mp4
# self-contained lower-third (replace with web/lower-third.html for the full OHG look)
cat > "$BASE/graphics/lower-third.html" <<'HTML'
<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;width:1920px;height:1080px;background:transparent;overflow:hidden;font-family:Arial,Helvetica,sans-serif}
#bar{position:absolute;left:120px;bottom:96px}
#name{background:#111;color:#fff;font-size:44px;font-weight:800;padding:10px 26px;border-left:10px solid #5b8cff}
#sub{background:#5b8cff;color:#fff;font-size:24px;font-weight:600;padding:6px 26px}
#clock{position:absolute;right:120px;bottom:96px;background:#111;color:#9d6bff;font:600 34px monospace;padding:12px 20px}
</style><div id=bar><div id=name>MXL SWITCHER</div><div id=sub>quickstart — cloud shared-memory production</div></div>
<div id=clock></div><script>setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString([],{hour12:false})},250)</script>
HTML
pkill -f "http.server 8085" 2>/dev/null || true
nohup python3 -m http.server 8085 --directory "$BASE/graphics" --bind 0.0.0.0 >/tmp/quickstart-graphics.log 2>&1 &
echo "  ✓ clip: $CLIP · graphics on :8085"

# ── 2. containers (exact wiring of the live mxlswitcher.com deployment) ─────
step "Containers"
docker rm -f $CONTAINERS >/dev/null 2>&1 || true
for i in $IMAGES; do docker pull -q "$i" >/dev/null & done; wait
docker run -d --name mediamtx --network host --restart unless-stopped \
  -e MTX_WEBRTCADDITIONALHOSTS="$PUBLIC_IP" bluenviron/mediamtx:latest >/dev/null
run_mf(){ # name hostport image extra...
  local name=$1 port=$2 image=$3; shift 3
  docker run -d --name "$name" --restart unless-stopped \
    -v "$DOMAIN_HOST":/mxl-domain -e MXL_DOMAIN=/mxl-domain \
    -p "$port":9600 "$@" "$image" >/dev/null
}
run_mf test-generator 9600 ghcr.io/cbcrc/test-generator:latest
run_mf file-player    9602 ghcr.io/cbcrc/file-player:latest    -v "$BASE/clips":/home/file:ro
run_mf input-selector 9604 ghcr.io/cbcrc/input-selector:latest -e MAX_INPUTS=7
run_mf html5-keyer    9605 ghcr.io/cbcrc/html5-keyer:latest    -e KEYER_DEFAULT_MODE=key --add-host host.docker.internal:host-gateway
docker run -d --name mxl2webrtc --restart unless-stopped \
  -v "$DOMAIN_HOST":/mxl-domain:ro -e MXL_DOMAIN=/mxl-domain \
  -e MEDIAMTX_WHIP_URL=http://host.docker.internal:8889/mxl2webrtc/whip \
  --add-host host.docker.internal:host-gateway \
  -p 9601:9600 $(for p in $(seq 8200 8210); do echo -n "-p $p:$p/udp "; done) \
  ghcr.io/cbcrc/mxl2webrtc:latest >/dev/null
for p in 9600 9601 9602 9604 9605; do
  for i in $(seq 1 45); do curl -s -m 2 -o /dev/null "http://127.0.0.1:$p/pipeline/status" && break; sleep 2; done
  curl -s -m 2 -o /dev/null "http://127.0.0.1:$p/pipeline/status" || die "API on :$p never came up — docker logs the container mapped to it"
done
echo "  ✓ all pipeline APIs answering"

# ── 3. writers → selector → keyer → encoder ──────────────────────────────────
# Flow UUIDs are discovered at runtime with mxl-info (labels below are free to
# change — discovery follows them). Order matters: writers first.
step "Pipelines"
post 9600/pipeline/start '{"domain":"/mxl-domain","grouphint":"Pattern","resolution":"1920x1080","framerate":"30","video":{"active":true,"description":"test pattern","label":"Pattern Video"},"audio1":{"active":true,"description":"tone","label":"Pattern Audio","channels":2},"audio2":{"active":false,"description":"","label":""},"ancillary":{"active":false,"description":"","label":""}}' >/dev/null
post 9600/video/test-pattern '{"pattern":"100% bars"}' >/dev/null
post 9602/pipeline/start "{\"domain\":\"/mxl-domain\",\"file\":\"$CLIP\",\"grouphint\":\"Playout\",\"video\":{\"active\":true,\"description\":\"sample clip\",\"label\":\"Clip Video\"},\"audio\":{\"active\":true,\"description\":\"clip audio\",\"label\":\"Clip Audio\"}}" >/dev/null
sleep 5
flow_id(){ docker exec input-selector sh -c "/opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l" 2>/dev/null \
  | grep -E "^\s+(Video|Audio) : [0-9a-f-]{36} - $1\$" | grep -oE '[0-9a-f-]{36}' | head -1; }
PATTERN=$(flow_id "Pattern Video"); CLIPV=$(flow_id "Clip Video")
[ -n "$PATTERN" ] && [ -n "$CLIPV" ] || die "source flows didn't appear — check: docker logs test-generator file-player"
echo "  ✓ sources:  pattern=$PATTERN  clip=$CLIPV"
post 9604/pipeline/start "{\"domain_path\":\"/mxl-domain\",\"input_flow_uuids\":[\"$PATTERN\",\"$CLIPV\"],\"grouphint\":\"Selector\",\"description\":\"program out\",\"label\":\"Selector PGM\"}" >/dev/null
sleep 2; post 9604/pipeline/active-input '{"slot":0}' >/dev/null
SEL=$(flow_id "Selector PGM"); [ -n "$SEL" ] || die "selector flow missing"
post 9605/pipeline/start "{\"mode\":\"key\",\"domain_path\":\"/mxl-domain\",\"input_flow_uuid\":\"$SEL\",\"html5_url\":\"http://host.docker.internal:8085/lower-third.html\",\"grouphint\":\"Keyer\",\"description\":\"program + graphics\",\"label\":\"Keyer PGM\"}" >/dev/null
post 9605/pipeline/key '{"on":true}' >/dev/null
sleep 4
KEY=$(flow_id "Keyer PGM"); [ -n "$KEY" ] || die "keyer flow missing — check: docker logs html5-keyer"
post 9601/pipeline/start "{\"domain_path\":\"/mxl-domain\",\"video_flow_uuid\":\"$KEY\",\"use_mediamtx\":true,\"encoder\":{\"tune\":4,\"speed_preset\":2,\"bitrate\":6000,\"key_int_max\":30}}" >/dev/null
echo "  ✓ selector → keyer → encoder running"

# ── 4. done ───────────────────────────────────────────────────────────────────
sleep 4
cat <<EOF

✅ YOUR MXL SWITCHER IS ON AIR
   Watch the program:   http://$PUBLIC_IP:8889/mxl2webrtc/
   (black video? open 8889/tcp AND 8189/udp in your cloud firewall)

   CUT to the clip:     curl -X POST -H 'Content-Type: application/json' -d '{"slot":1}' http://127.0.0.1:9604/pipeline/active-input
   CUT to the pattern:  curl -X POST -H 'Content-Type: application/json' -d '{"slot":0}' http://127.0.0.1:9604/pipeline/active-input
   Change the pattern:  curl -X POST -H 'Content-Type: application/json' -d '{"pattern":"Pinwheel"}' http://127.0.0.1:9600/video/test-pattern
   Graphics off/on:     curl -X POST -H 'Content-Type: application/json' -d '{"on":false}' http://127.0.0.1:9605/pipeline/key

   Every pixel above crossed a shared-memory MXL domain at $DOMAIN_HOST
   List the flows:      docker exec input-selector /opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l

   NEXT STEPS (docs/QUICKSTART.md): add your own camera over SRT, thumbnails,
   layouts, a multiview wall, guest contribution — tools/ has every piece,
   scripts/bring-up-mxl.sh shows the full assembly, docs/FINDINGS.md saves
   you the outages. Teardown: sudo $0 --down
EOF
