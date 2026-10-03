#!/usr/bin/env bash
# quickstart.sh — fresh Ubuntu VM → cuttable MXL switcher in one command.
#
#   git clone https://github.com/guycochran/mxl-cloud-production-demo
#   cd mxl-cloud-production-demo
#   sudo scripts/quickstart.sh
#
# Clone, don't curl|bash: the guest-contribution feature needs files from the
# repo (docker/guest-ingest.Dockerfile, tools/), so a piped run silently skips
# the phone-on-air slots — and piping master straight into root shell is a worse
# security posture anyway.
#
# What you get: an EBU MXL shared-memory domain with a test generator, a file
# player (sample clip auto-downloaded), a 7-slot input selector, an HTML5
# graphics keyer (lower-third + clock), a WebRTC encoder, and two SRT guest
# slots — all stock ghcr.io/cbcrc containers — plus the curl one-liners to CUT
# between sources and a browser URL to watch the program. No external services.
#
# Requirements: Ubuntu 22.04/24.04, x86-64 CPU **with AVX** (any Azure
# D-series v5, AWS m5/m6i, GCP n2; QEMU needs `-cpu host` — see FINDINGS §11),
# ~8 vCPU recommended, ports open in your cloud firewall:
#   8889/tcp (WebRTC page+signaling)  8189/udp (WebRTC media)
#   8890/udp (optional: SRT contribution for your own camera)
#
# Idempotent: safe to re-run. Teardown: sudo scripts/quickstart.sh --down
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"      # repo scripts/ dir (for the Dockerfile + watcher)
REPO="$(cd "$HERE/.." && pwd)"
DOMAIN_HOST=/dev/shm/mxl/domain_1          # shared-memory domain (volatile: gone on reboot — just re-run)
BASE=/srv/mxl-quickstart                   # clips + graphics live here (persistent)
# ── images: PINNED BY DIGEST for reproducibility (docs/VERSIONS.md) ─────────────
# Floating :latest can break a quickstart on an upstream rebuild that has nothing to
# do with this repo. These digests are what the Oct 2026 cold-clone proofs ran
# against. Opt into current upstream with:  sudo MXL_BLEEDING_EDGE=1 scripts/quickstart.sh
IMG_MEDIAMTX_PIN="bluenviron/mediamtx@sha256:5ce2a948eb68df06ce2e13870db8df8e30d516ac4dc40e04bfe8aee3bdf7be40"
IMG_TESTGEN_PIN="ghcr.io/cbcrc/test-generator@sha256:09cad0981475095ab948ca51511d4fbdc0521e2a23632d50abaf14fc3847cd92"
IMG_FILEPLAYER_PIN="ghcr.io/cbcrc/file-player@sha256:149953ce851a6d5e2ffbc9bd39c7e82ae529af25c20237457a77033c24e8f297"
IMG_SELECTOR_PIN="ghcr.io/cbcrc/input-selector@sha256:c1e869ea39985ae195951a1d3d78e08001e4521017acad0632a79d48b936e3d1"
IMG_KEYER_PIN="ghcr.io/cbcrc/html5-keyer@sha256:419ac23e1d75ad0c94b437dd709cc0d701b0dacfe3679733b5658a530bc86660"
IMG_WEBRTC_PIN="ghcr.io/cbcrc/mxl2webrtc@sha256:ca047e75714bfad97239060f35d7e96e37decc6dacdf747c539940cc59fd37f6"
if [ "${MXL_BLEEDING_EDGE:-0}" = 1 ]; then
  echo "  ⚠ MXL_BLEEDING_EDGE=1 — using upstream :latest (not the pinned, proven digests)"
  IMG_MEDIAMTX=bluenviron/mediamtx:latest
  IMG_TESTGEN=ghcr.io/cbcrc/test-generator:latest
  IMG_FILEPLAYER=ghcr.io/cbcrc/file-player:latest
  IMG_SELECTOR=ghcr.io/cbcrc/input-selector:latest
  IMG_KEYER=ghcr.io/cbcrc/html5-keyer:latest
  IMG_WEBRTC=ghcr.io/cbcrc/mxl2webrtc:latest
else
  IMG_MEDIAMTX="$IMG_MEDIAMTX_PIN"; IMG_TESTGEN="$IMG_TESTGEN_PIN"
  IMG_FILEPLAYER="$IMG_FILEPLAYER_PIN"; IMG_SELECTOR="$IMG_SELECTOR_PIN"
  IMG_KEYER="$IMG_KEYER_PIN"; IMG_WEBRTC="$IMG_WEBRTC_PIN"
fi
IMAGES="$IMG_MEDIAMTX $IMG_TESTGEN $IMG_FILEPLAYER $IMG_SELECTOR $IMG_KEYER $IMG_WEBRTC"
CONTAINERS="mediamtx test-generator file-player input-selector html5-keyer mxl2webrtc guest1 guest2 guest1-audio guest2-audio"
# Guest image tag ENCODES THE MODE so a pinned run can't silently reuse an
# edge-built base (or vice versa): `docker image inspect` keys on the tag, so
# distinct tags = distinct cache entries. Pinned tag carries the base digest's
# short id; edge is its own tag.
if [ "${MXL_BLEEDING_EDGE:-0}" = 1 ]; then
  GUEST_IMAGE=mxl-guest-ingest:edge
else
  GUEST_IMAGE="mxl-guest-ingest:pinned-$(printf '%s' "$IMG_TESTGEN_PIN" | sed 's/.*@sha256://' | cut -c1-12)"
fi
# Guest flow UUIDs from the facility manifest (config/facility.json) — the single
# source of truth shared with the tools + backend. Fallback to the facility
# standard (guest2 = ...01, matching every other consumer) if python/manifest is
# unavailable. NOTE: quickstart previously used ...02 for guest2 here only; it was
# self-contained (written+read within this script) so it worked, but was the one
# value out of step with the rest of the facility — aligned to ...01.
_qfac_sec() {  # _qfac_sec <section> <name> <fallback>
  python3 - "$REPO/config/facility.json" "$1" "$2" "$3" 2>/dev/null <<'PY' || printf '%s' "$3"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    sys.stdout.write(d[sys.argv[2]][sys.argv[3]]["uuid"])
except Exception:
    sys.exit(1)
PY
}
_qfac()       { _qfac_sec video_flows "$1" "$2"; }  # <name> <fallback>
_qfac_audio() { _qfac_sec audio_flows "$1" "$2"; }  # <name> <fallback>
GUEST1_FLOW=$(_qfac guest1 9e111e00-aaaa-4bbb-8ccc-000000000001)
GUEST2_FLOW=$(_qfac guest2 9e222e00-aaaa-4bbb-8ccc-000000000001)
# audio flows (v0.3 A/V guest legs) — same manifest, audio_flows section
GUEST1_AUDIO_FLOW=$(_qfac_audio guest1 a1111e00-aaaa-4bbb-8ccc-000000000001)
GUEST2_AUDIO_FLOW=$(_qfac_audio guest2 a2222e00-aaaa-4bbb-8ccc-000000000001)
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
  pkill -f "guest_slot_watcher.py" 2>/dev/null || true
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
# wait ONLY on the pulls — a bare `wait` also waits on the :8085 graphics
# server backgrounded above, which never exits (hung the script; found 9/13)
pull_pids=(); for i in $IMAGES; do docker pull -q "$i" >/dev/null & pull_pids+=($!); done
wait "${pull_pids[@]}"
docker run -d --name mediamtx --network host --restart unless-stopped \
  -e MTX_WEBRTCADDITIONALHOSTS="$PUBLIC_IP" "$IMG_MEDIAMTX" >/dev/null
run_mf(){ # name hostport image extra...
  local name=$1 port=$2 image=$3; shift 3
  # Control APIs bind 127.0.0.1 ONLY (least privilege): these are unauthenticated
  # media-function control ports — never expose them on all interfaces. Media ports
  # (WebRTC/SRT below) stay world-facing on purpose. Any public control surface is a
  # separate, deliberately-authenticated layer.
  docker run -d --name "$name" --restart unless-stopped \
    -v "$DOMAIN_HOST":/mxl-domain -e MXL_DOMAIN=/mxl-domain \
    -p 127.0.0.1:"$port":9600 "$@" "$image" >/dev/null
}
run_mf test-generator 9600 "$IMG_TESTGEN"
run_mf file-player    9602 "$IMG_FILEPLAYER"  -v "$BASE/clips":/home/file:ro
run_mf input-selector 9604 "$IMG_SELECTOR"    -e MAX_INPUTS=7
run_mf html5-keyer    9605 "$IMG_KEYER"       -e KEYER_DEFAULT_MODE=key --add-host host.docker.internal:host-gateway
docker run -d --name mxl2webrtc --restart unless-stopped \
  -v "$DOMAIN_HOST":/mxl-domain:ro -e MXL_DOMAIN=/mxl-domain \
  -e MEDIAMTX_WHIP_URL=http://host.docker.internal:8889/mxl2webrtc/whip \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:9601:9600 $(for p in $(seq 8200 8210); do echo -n "-p $p:$p/udp "; done) \
  "$IMG_WEBRTC" >/dev/null
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

# ── 3b. guest contribution: phone/OBS SRT → cuttable Guest slots ──────────────
# The fastest way for a newcomer to put THEIR OWN video on screen: scan a Larix
# QR (or paste the SRT URL into OBS/vMix/ffmpeg) → it lands as a cuttable button.
# No camera, no RTSP, no config. mediamtx (already up, host net) accepts the SRT
# publish; a guest-ingest container conforms it to the domain and a backend-free
# watcher re-attaches the selector the moment the flow appears.
step "Guest contribution (SRT)"
if ! docker image inspect "$GUEST_IMAGE" >/dev/null 2>&1; then
  if [ -f "$REPO/docker/guest-ingest.Dockerfile" ]; then
    echo "  building $GUEST_IMAGE (one-time)…"
    # keep the guest base consistent with the mode: pinned digest by default, the
    # test-generator :latest under MXL_BLEEDING_EDGE (Dockerfile ARG BASE default = pin).
    build_base_arg=(); [ "${MXL_BLEEDING_EDGE:-0}" = 1 ] && build_base_arg=(--build-arg BASE=ghcr.io/cbcrc/test-generator:latest)
    docker build -q "${build_base_arg[@]}" -f "$REPO/docker/guest-ingest.Dockerfile" -t "$GUEST_IMAGE" "$REPO" >/dev/null \
      || { echo "  ⚠ guest image build failed — skipping guest slots (core switcher still on air)"; GUEST_IMAGE=""; }
  else
    echo "  ⚠ docker/guest-ingest.Dockerfile not found (running via curl-pipe?) — skipping guest slots"; GUEST_IMAGE=""
  fi
fi
if [ -n "$GUEST_IMAGE" ]; then
  # one container per guest slot. An idle guest's rtspsrc 404s and the ingest
  # exits — expected. We retry it in a TIGHT 2s loop (NOT docker's exponential
  # backoff, which grows to 10-20s+ and makes a connecting phone wait or miss its
  # window) so the pipeline rebuilds within ~2s of a publisher appearing. This is
  # the same supervisor pattern the live demo uses (run-cam1.sh). The loop owns
  # liveness, so no docker --restart policy. ~1000ms jitterbuffer = cellular SRT.
  run_guest(){ # name srt-stream flow label
    docker rm -f "$1" >/dev/null 2>&1 || true
    docker run -d --name "$1" \
      -v "$DOMAIN_HOST":/mxl-domain -e MXL_DOMAIN=/mxl-domain -e MXL_REPAIR_URL=none \
      --add-host host.docker.internal:host-gateway --entrypoint sh \
      "$GUEST_IMAGE" -c "while :; do python3 guest_ingest.py \"\$0\" \"\$1\" \"\$2\" 1000; sleep 2; done" \
      "$2" "$3" "$4" >/dev/null
  }
  # AUDIO leg (v0.3): a guest is A/V, so pair the video ingest with an audio one —
  # same mediamtx path, the AudioGuestAdapter via guest_audio.py. On the single-box
  # quickstart the source host is host.docker.internal (not the VNet 10.0.0.5 the
  # live facility uses), so pass it explicitly. The program-audio mixer tolerates an
  # absent audio flow, so this is additive — a guest still cuts video-only if audio
  # is off. Set MXL_GUEST_AUDIO=0 to skip (e.g. a video-only test).
  run_guest_audio(){ # name srt-stream flow label
    docker rm -f "$1-audio" >/dev/null 2>&1 || true
    docker run -d --name "$1-audio" \
      -v "$DOMAIN_HOST":/mxl-domain -e MXL_DOMAIN=/mxl-domain -e MXL_REPAIR_URL=none \
      -e MXL_AUDIO_RTSP_HOST=host.docker.internal \
      --add-host host.docker.internal:host-gateway --entrypoint sh \
      "$GUEST_IMAGE" -c "while :; do python3 guest_audio.py \"\$0\" \"\$1\" \"\$2\" 1000; sleep 3; done" \
      "$2" "$3" "$4" >/dev/null
  }
  run_guest guest1 guest1 "$GUEST1_FLOW" "Guest 1"
  run_guest guest2 guest2 "$GUEST2_FLOW" "Guest 2"
  if [ "${MXL_GUEST_AUDIO:-1}" = 1 ]; then
    run_guest_audio guest1 guest1 "$GUEST1_AUDIO_FLOW" "Guest 1 Audio"
    run_guest_audio guest2 guest2 "$GUEST2_AUDIO_FLOW" "Guest 2 Audio"
  fi
  # backend-free selector re-attach: wires a Guest flow into the selector the
  # instant it appears (and drops it when it goes). Runs on the host, stdlib only.
  pkill -f "guest_slot_watcher.py" 2>/dev/null || true
  BASE_LABELS="Pattern Video,Clip Video" GUEST_LABELS="Guest 1,Guest 2" \
    nohup python3 "$REPO/tools/guest_slot_watcher.py" >/tmp/quickstart-guest-watcher.log 2>&1 &
  _audio_note=$([ "${MXL_GUEST_AUDIO:-1}" = 1 ] && echo "+audio" || echo "video-only")
  echo "  ✓ Guest 1/2 slots armed ($_audio_note) · SRT publish point: srt://$PUBLIC_IP:8890 · watcher live"
fi

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

   📱 PUT YOUR OWN FACE ON AIR (no camera needed):
      Install "Larix Broadcaster" (free, iOS/Android). New connection → SRT →
         URL:  srt://$PUBLIC_IP:8890
         Mode: Caller   ·   Stream ID:  publish:guest1   (or publish:guest2)
      Tap to go live → it appears as Guest 1, cuttable like any source:
         curl -X POST -H 'Content-Type: application/json' -d '{"slot":2}' http://127.0.0.1:9604/pipeline/active-input
      (OBS/vMix/ffmpeg work too — same URL. Open 8890/udp in your cloud firewall.)

   Every pixel above crossed a shared-memory MXL domain at $DOMAIN_HOST
   List the flows:      docker exec input-selector /opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l

   NEXT STEPS (docs/QUICKSTART.md): add your own camera over SRT, thumbnails,
   layouts, a multiview wall, guest contribution — tools/ has every piece,
   scripts/bring-up-mxl.sh shows the full assembly, docs/FINDINGS.md saves
   you the outages. Teardown: sudo $0 --down
EOF
