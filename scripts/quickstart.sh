#!/usr/bin/env bash
# quickstart.sh — fresh Ubuntu VM → cuttable MXL switcher in one command.
#
#   git clone https://github.com/guycochran/mxl-switcher
#   cd mxl-switcher
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
#   + 8891/udp when MXL_GUEST_TRANSPORT=srt-listen (guest2's own listener port)
#
# Guest transport (MXL_GUEST_TRANSPORT): srt-direct (default, via mediamtx) |
#   srt-listen (ingest listens directly, mediamtx out of the contribution path —
#   one UDP port per guest: 8890/8891) | rtsp (legacy).
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
# Guest ingest transport (MXL_GUEST_TRANSPORT):
#   srt-direct (DEFAULT) — contributor publishes to mediamtx (streamid publish:guestN),
#     the ingest reads BACK from mediamtx over SRT. Multiplexes all guests on one port
#     (8890). The hardware-proven default.
#   srt-listen — the ingest IS the SRT listener; the contributor's caller lands STRAIGHT
#     on it, mediamtx is OUT of the contribution path ("nothing in the way"). Fixes the
#     read-back -5 restart-loop on jittery sources (see tools/adapters.py). One listener
#     owns one UDP port, so guestN binds 8890+(N-1): guest1=8890, guest2=8891 — open BOTH
#     in your cloud firewall. mediamtx's own SRT server is disabled (its WebRTC stays up).
#     Audio rides the same MPEG-TS as video (one listener), so the separate audio leg is
#     video-only in this mode — the program mixer tolerates an absent guest-audio flow.
#   rtsp — legacy rtspsrc path.
GUEST_TRANSPORT="${MXL_GUEST_TRANSPORT:-srt-direct}"
GUEST_LISTEN_BASE_PORT="${MXL_GUEST_LISTEN_BASE_PORT:-8890}"
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
  pkill -f "http.server 8085|graphics_server.py" 2>/dev/null || true
  pkill -f "http.server 8086" 2>/dev/null || true
  pkill -f "guest_slot_watcher.py" 2>/dev/null || true
  pkill -f "backend/local-server.js" 2>/dev/null || true
  pkill -f "mxl-selfheal.sh" 2>/dev/null || true
  # Wipe the shared-memory domain. A flow's writer lives in this shared memory, not
  # in the process — so a container that was killed mid-stream (or a guest that
  # dropped) leaves a flow dir behind whose writer is dead but still "owned". The
  # next run's mxlsink then fails with "the UUID belongs to a flow with another
  # active writer" and the source never attaches. Clearing the domain on --down
  # means the next `up` always starts from a clean slate. (Volatile by design —
  # it's gone on reboot anyway; $BASE with your clips/graphics is kept.)
  rm -rf "$DOMAIN_HOST" 2>/dev/null || true
  echo "Done. (domain wiped for a clean restart; $BASE kept — rm -rf $BASE to remove clips/graphics too.)"
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
pkill -f "http.server 8085|graphics_server.py" 2>/dev/null || true
# Graphics server (:8085) — only the html5-keyer CONTAINER needs to reach it (via
# host.docker.internal -> the docker bridge gateway). So by default we bind to that
# bridge address, NOT 0.0.0.0: unreachable from the internet, still reachable from
# containers. (127.0.0.1 would break the keyer: containers can't reach host loopback.)
#   MXL_GRAPHICS_BIND=0.0.0.0     opt back in to the old all-interfaces behaviour
#   MXL_GRAPHICS_BIND=<ip>        bind a specific address (e.g. 127.0.0.1 if you
#                                 front the keyer differently)
# Directory listing is disabled (files are served by exact name only).
GRAPHICS_BIND="${MXL_GRAPHICS_BIND:-}"
if [ -z "$GRAPHICS_BIND" ]; then
  GRAPHICS_BIND=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)
  [ -n "$GRAPHICS_BIND" ] || GRAPHICS_BIND=172.17.0.1
fi
cat > "$BASE/graphics_server.py" <<'PY'
import http.server, os, sys
# usage: graphics_server.py <port> <bind> <directory>
class NoListing(http.server.SimpleHTTPRequestHandler):
    """Static files by exact name; directories 404 (no index/listing)."""
    def list_directory(self, path):
        self.send_error(404, "Not found")
        return None
    def send_head(self):
        p = self.translate_path(self.path)
        if os.path.isdir(p):
            self.send_error(404, "Not found")
            return None
        return super().send_head()
port, bind, root = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.chdir(root)
http.server.ThreadingHTTPServer((bind, port), NoListing).serve_forever()
PY
nohup python3 "$BASE/graphics_server.py" 8085 "$GRAPHICS_BIND" "$BASE/graphics" >/tmp/quickstart-graphics.log 2>&1 &
sleep 1
kill -0 $! 2>/dev/null || { echo "  ⚠ graphics server failed to bind $GRAPHICS_BIND:8085 — see /tmp/quickstart-graphics.log (retry with MXL_GRAPHICS_BIND=0.0.0.0)"; }
[ "$GRAPHICS_BIND" = 0.0.0.0 ] && echo "  ⚠ MXL_GRAPHICS_BIND=0.0.0.0: graphics server is reachable from all interfaces"
echo "  ✓ clip: $CLIP · graphics on $GRAPHICS_BIND:8085"

# ── 2. containers (exact wiring of the live mxlswitcher.com deployment) ─────
step "Containers"
docker rm -f $CONTAINERS >/dev/null 2>&1 || true
# wait ONLY on the pulls — a bare `wait` also waits on the :8085 graphics
# server backgrounded above, which never exits (hung the script; found 9/13)
pull_pids=(); for i in $IMAGES; do docker pull -q "$i" >/dev/null & pull_pids+=($!); done
wait "${pull_pids[@]}"
# ── optional per-guest SRT publish passphrase (default: OFF, open contribution) ──
#   MXL_GUEST1_SRT_PASSPHRASE / MXL_GUEST2_SRT_PASSPHRASE   per-slot secret
#   MXL_GUEST_SRT_PASSPHRASE                                 same secret for both
# SRT passphrases are 10–79 chars. When set, mediamtx only accepts a publisher on that
# slot whose SRT connection is encrypted with the passphrase (Larix: Passphrase field;
# ffmpeg: ...&passphrase=SECRET). Our own readers (guest ingest) are unaffected. When
# NOTHING is set no config is mounted and behaviour is byte-for-byte unchanged.
# --- BEGIN guest-srt-conf (extracted by tests/test_quickstart_hardening.py) ---
_srt_pass_for(){ # <slot 1|2>  → passphrase (per-slot wins over the shared one)
  local v="MXL_GUEST${1}_SRT_PASSPHRASE"
  printf '%s' "${!v:-${MXL_GUEST_SRT_PASSPHRASE:-}}"
}
_srt_pass_ok(){ # 10–79 chars, no quote/backslash/newline/control chars (goes into YAML)
  local n=${#1}
  [ "$n" -ge 10 ] && [ "$n" -le 79 ] || return 1
  case "$1" in *\'*|*\"*|*\\*|*$'\n'*|*$'\r'*|*$'\t'*) return 1;; esac
  return 0
}
render_mediamtx_guest_conf(){ # prints YAML on stdout; empty output = no auth configured
  local i p out=""
  for i in 1 2; do
    p=$(_srt_pass_for "$i")
    [ -n "$p" ] || continue
    _srt_pass_ok "$p" || { echo "✗ guest $i SRT passphrase must be 10-79 chars with no quotes/backslashes/control chars" >&2; return 1; }
    out+="  guest$i:"$'\n'"    srtPublishPassphrase: '$p'"$'\n'
  done
  [ -n "$out" ] && printf 'paths:\n%s  all_others:\n' "$out"
  return 0
}
# --- END guest-srt-conf ---
MTX_CONF_ARGS=()
GUEST_CONF=$(render_mediamtx_guest_conf) || exit 1
if [ "$GUEST_TRANSPORT" = srt-listen ]; then
  # mediamtx's SRT server is disabled in listen mode, so its publish passphrase can't
  # apply — the contributor connects to the ingest's srtsrc listener, not mediamtx.
  if [ -n "$GUEST_CONF" ]; then
    echo "  ⚠ MXL_GUEST_*_SRT_PASSPHRASE is ignored in srt-listen mode (mediamtx SRT is off); gate the listener with the SRT passphrase on the publisher side, or restrict the port in your firewall"
  else
    echo "  ⚠ guest SRT slots are OPEN (srt-listen: any caller reaching the port). Restrict the UDP port in your cloud firewall to limit who can publish"
  fi
elif [ -n "$GUEST_CONF" ]; then
  umask 077; printf '%s\n' "$GUEST_CONF" > "$BASE/mediamtx.guest-auth.yml"; umask 022
  MTX_CONF_ARGS=(-v "$BASE/mediamtx.guest-auth.yml":/mediamtx.yml:ro)
  echo "  ✓ SRT publish passphrase REQUIRED for: $([ -n "$(_srt_pass_for 1)" ] && printf 'guest1 ')$([ -n "$(_srt_pass_for 2)" ] && printf 'guest2')"
else
  echo "  ⚠ guest SRT slots are OPEN (any publisher who knows the stream id). Set MXL_GUEST_SRT_PASSPHRASE to require one — see SECURITY.md"
fi
# In srt-listen mode the guest ingests own the SRT port(s) directly, so mediamtx must
# NOT bind its own SRT server (would collide on 8890). Disable it with MTX_SRT=no —
# mediamtx keeps serving WebRTC (8889/8189), which the program monitor still needs.
MTX_SRT_ARGS=()
[ "$GUEST_TRANSPORT" = srt-listen ] && MTX_SRT_ARGS=(-e MTX_SRT=no)
docker run -d --name mediamtx --network host --restart unless-stopped "${MTX_CONF_ARGS[@]}" \
  "${MTX_SRT_ARGS[@]}" -e MTX_WEBRTCADDITIONALHOSTS="$PUBLIC_IP" "$IMG_MEDIAMTX" >/dev/null
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
  # Guests default to SRT-direct (MXL_GUEST_TRANSPORT unset -> srt-direct): read the
  # stream straight from mediamtx over SRT (srtsrc!tsdemux) — the hardware-proven path
  # that avoids the RTSP two-track flap. Source host = host.docker.internal (the single
  # box). Set MXL_GUEST_TRANSPORT=rtsp for the legacy path, or =srt-listen for the
  # direct-listener path (the ingest binds its own SRT port; see the header note).
  run_guest(){ # name srt-stream flow label [listen-port]
    docker rm -f "$1" >/dev/null 2>&1 || true
    # srt-listen binds a UDP port on the host, so the container needs host networking
    # (so srtsrc listener is reachable from the internet on that port). srt-direct dials
    # OUT to mediamtx, so host.docker.internal + the bridge is fine.
    local net_args=(--add-host host.docker.internal:host-gateway)
    local port_env=()
    if [ "$GUEST_TRANSPORT" = srt-listen ]; then
      net_args=(--network host)
      port_env=(-e "MXL_GUEST_LISTEN_PORT=${5:-$GUEST_LISTEN_BASE_PORT}")
    fi
    docker run -d --name "$1" \
      -v "$DOMAIN_HOST":/mxl-domain -e MXL_DOMAIN=/mxl-domain -e MXL_REPAIR_URL=none \
      -e MXL_GUEST_HOST=host.docker.internal \
      -e "MXL_GUEST_TRANSPORT=$GUEST_TRANSPORT" "${port_env[@]}" \
      "${net_args[@]}" --entrypoint sh \
      "$GUEST_IMAGE" -c "while :; do python3 guest_ingest.py \"\$0\" \"\$1\" \"\$2\" 1000; sleep 2; done" \
      "$2" "$3" "$4" >/dev/null
  }
  # AUDIO leg (v0.3): a guest is A/V, so pair the video ingest with an audio one —
  # same mediamtx path, via guest_audio.py (also SRT-direct by default). The program-
  # audio mixer tolerates an absent audio flow, so this is additive — a guest still
  # cuts video-only if audio is off. Set MXL_GUEST_AUDIO=0 to skip.
  run_guest_audio(){ # name srt-stream flow label
    docker rm -f "$1-audio" >/dev/null 2>&1 || true
    docker run -d --name "$1-audio" \
      -v "$DOMAIN_HOST":/mxl-domain -e MXL_DOMAIN=/mxl-domain -e MXL_REPAIR_URL=none \
      -e MXL_GUEST_HOST=host.docker.internal \
      -e "MXL_GUEST_TRANSPORT=${MXL_GUEST_TRANSPORT:-srt-direct}" \
      --add-host host.docker.internal:host-gateway --entrypoint sh \
      "$GUEST_IMAGE" -c "while :; do python3 guest_audio.py \"\$0\" \"\$1\" \"\$2\" 1000; sleep 3; done" \
      "$2" "$3" "$4" >/dev/null
  }
  # srt-listen: one UDP port per guest listener (guest1=base, guest2=base+1). srt-direct:
  # both multiplex on mediamtx:8890 (the trailing port arg is ignored by run_guest).
  run_guest guest1 guest1 "$GUEST1_FLOW" "Guest 1" "$GUEST_LISTEN_BASE_PORT"
  run_guest guest2 guest2 "$GUEST2_FLOW" "Guest 2" "$((GUEST_LISTEN_BASE_PORT + 1))"
  # AUDIO leg: a separate audio listener can't share the video listener's port, and the
  # contributor's MPEG-TS already carries audio alongside video — so the standalone audio
  # leg only applies to srt-direct/rtsp (where it reads its own stream from mediamtx). In
  # srt-listen mode guests are video-only for now; the mixer tolerates an absent flow.
  if [ "${MXL_GUEST_AUDIO:-1}" = 1 ] && [ "$GUEST_TRANSPORT" != srt-listen ]; then
    run_guest_audio guest1 guest1 "$GUEST1_AUDIO_FLOW" "Guest 1 Audio"
    run_guest_audio guest2 guest2 "$GUEST2_AUDIO_FLOW" "Guest 2 Audio"
  fi
  # backend-free selector re-attach: wires a Guest flow into the selector the
  # instant it appears (and drops it when it goes). Runs on the host, stdlib only.
  pkill -f "guest_slot_watcher.py" 2>/dev/null || true
  BASE_LABELS="Pattern Video,Clip Video" GUEST_LABELS="Guest 1,Guest 2" \
    nohup python3 "$REPO/tools/guest_slot_watcher.py" >/tmp/quickstart-guest-watcher.log 2>&1 &
  if [ "$GUEST_TRANSPORT" = srt-listen ]; then
    _g2port="$((GUEST_LISTEN_BASE_PORT + 1))"
    echo "  ✓ Guest 1/2 slots armed (video-only, SRT-direct-LISTEN · mediamtx SRT off) · publish straight to the ingest: guest1 srt://$PUBLIC_IP:$GUEST_LISTEN_BASE_PORT · guest2 srt://$PUBLIC_IP:$_g2port · watcher live"
    echo "    ⚠ open UDP $GUEST_LISTEN_BASE_PORT AND $_g2port in your cloud firewall (one port per guest in listen mode)"
  else
    _audio_note=$([ "${MXL_GUEST_AUDIO:-1}" = 1 ] && echo "+audio" || echo "video-only")
    echo "  ✓ Guest 1/2 slots armed ($_audio_note) · SRT publish point: srt://$PUBLIC_IP:8890 · watcher live"
  fi
fi

# ── 3c. multiview thumbnails (opt-in, default on) ─────────────────────────────
# One low-rate JPEG per source (no decode, tiny CPU) into <domain>/thumbs, served
# on 127.0.0.1:8086 for the control UI's multiview grid. mxl_thumbs.py needs
# python3 + mxlsrc + the domain mount; that's the writer/ingest container (hls2mxl),
# NOT necessarily the selector — so pick whichever container actually has python3.
# Self-healing restart loop, mirrors scripts/bring-up-mxl.sh. Skips if no fit.
CONTROL_UI_URL=""
THUMBS_ORIGIN=""
if [ "${MXL_THUMBS:-1}" = 1 ] && [ -f "$REPO/tools/mxl_thumbs.py" ]; then
  step "Multiview thumbnails"
  # find a running container with python3 (hls2mxl first — it's the ingest box)
  THUMB_CTR=""
  for c in hls2mxl input-selector $CONTAINERS; do
    if docker exec "$c" sh -c 'command -v python3' >/dev/null 2>&1; then THUMB_CTR="$c"; break; fi
  done
  if [ -n "$THUMB_CTR" ] && docker cp "$REPO/tools/mxl_thumbs.py" "$THUMB_CTR":/tmp/mxl_thumbs.py 2>/dev/null; then
    docker exec "$THUMB_CTR" sh -c 'pkill -9 -f run-thumbs.sh; pkill -9 -f mxl_thumbs.py; true' 2>/dev/null || true
    docker exec "$THUMB_CTR" sh -c 'printf "#!/bin/sh\nwhile :; do nice -n 15 python3 /tmp/mxl_thumbs.py >> /tmp/mxl-thumbs.log 2>&1; echo RESTART >> /tmp/mxl-thumbs.log; sleep 3; done\n" > /tmp/run-thumbs.sh && chmod +x /tmp/run-thumbs.sh'
    docker exec -d "$THUMB_CTR" /tmp/run-thumbs.sh
    mkdir -p /srv/thumbs-www && ln -sfn "$DOMAIN_HOST/thumbs" /srv/thumbs-www/thumbs
    pgrep -f "http.server 8086" >/dev/null || \
      nohup python3 -m http.server 8086 --directory /srv/thumbs-www --bind 127.0.0.1 >/tmp/thumbs-8086.log 2>&1 &
    THUMBS_ORIGIN="http://127.0.0.1:8086/thumbs"   # jpgs served under /thumbs/<name>.jpg
    echo "  ✓ thumbnails live → 127.0.0.1:8086/thumbs ($THUMB_CTR, self-healing)"
  else
    echo "  ⚠ no container with python3 for mxl_thumbs.py — skipping thumbnails (tiles show 'no signal')"
  fi
fi

# ── 3d. browser control UI (opt-in, default on if node is present) ────────────
# The self-contained switcher: web/local.html driven by the open /api/mxl/* routes
# through backend/local-server.js. Localhost-only by default (no auth of its own —
# put it behind an SSH tunnel / reverse proxy to reach it remotely). Skips cleanly
# if node isn't installed; the raw-curl path below still works either way.
if [ "${MXL_CONTROL_UI:-1}" = 1 ] && command -v node >/dev/null 2>&1; then
  step "Browser control UI"
  if [ ! -d "$REPO/backend/node_modules/express" ]; then
    (cd "$REPO/backend" && npm install --no-audit --no-fund >/tmp/mxl-control-npm.log 2>&1) \
      || echo "  ⚠ npm install failed (see /tmp/mxl-control-npm.log) — UI may not start"
  fi
  # segno (pure-python QR) powers the "Add your camera" QR codes. Optional: if it
  # can't install, the UI still shows the copy-URL + typed fields (no QR, no wall).
  python3 -c "import segno" 2>/dev/null \
    || pip3 install -q segno 2>/dev/null \
    || python3 -m pip install -q --break-system-packages segno 2>/dev/null \
    || echo "  ⚠ segno not installed — 'Add your camera' shows the URL/fields without a QR"
  pkill -f "backend/local-server.js" 2>/dev/null || true
  CTRL_PORT="${MXL_CONTROL_PORT:-3100}"
  # Generate a facility manifest from the flows THIS box actually discovered, so the
  # UI's slots + cuts match the running selector (review R2 — the repo's
  # config/facility.json has the lab's fixed UUIDs, which don't exist on a fresh box).
  GEN_FACILITY="$BASE/facility.generated.json"
  if docker exec input-selector sh -c "/opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l" 2>/dev/null \
       | python3 "$REPO/tools/facility_from_discovery.py" --vm 127.0.0.1 > "$GEN_FACILITY" 2>/tmp/mxl-facility-gen.log; then
    echo "  ✓ facility manifest generated from discovered flows → $GEN_FACILITY"
  else
    echo "  ⚠ could not generate manifest from discovery (see /tmp/mxl-facility-gen.log) — UI falls back to repo manifest"
    GEN_FACILITY=""
  fi
  # Use `env` so a CONDITIONAL assignment works: a bare shell assignment-prefix word
  # that comes from a ${VAR:+...} expansion is NOT treated as an assignment (bash sees
  # it as the command to run → "MXL_FACILITY_JSON=…: No such file or directory" and the
  # UI never starts). `env` consumes every leading NAME=VALUE arg regardless of origin.
  # Pass the guest transport + base SRT port so the UI builds the right "Add your
  # camera" QR/URL: srt-direct → one port + streamid; srt-listen → per-guest port, no
  # streamid (matches run_guest above).
  nohup env MXL_VM_URL="http://127.0.0.1" \
    MXL_THUMBS_ORIGIN="${THUMBS_ORIGIN:-http://127.0.0.1:8086/thumbs}" \
    MXL_PROGRAM_ORIGIN="http://127.0.0.1:8889" MXL_CONTROL_PORT="$CTRL_PORT" \
    MXL_PUBLIC_IP="$PUBLIC_IP" MXL_GUEST_SRT_PORT="$GUEST_LISTEN_BASE_PORT" \
    MXL_GUEST_TRANSPORT="$GUEST_TRANSPORT" \
    ${GEN_FACILITY:+MXL_FACILITY_JSON="$GEN_FACILITY"} \
    node "$REPO/backend/local-server.js" >/tmp/mxl-control-ui.log 2>&1 &
  sleep 1
  CONTROL_UI_URL="http://127.0.0.1:$CTRL_PORT/"
  echo "  ✓ control UI → $CONTROL_UI_URL (localhost-only; tunnel it to drive remotely)"
fi

# ── 3e. self-healer (opt-in, default OFF) ─────────────────────────────────────
# The facility can drift: the selector pipeline may stop (every cut 409s) and the
# WebRTC relay can get stuck waiting for an audio flow a bare quickstart never
# starts (program shows "stream not found"). This backend-free watcher polls the
# local control ports and recovers both. It is OFF unless explicitly enabled —
# set MXL_SELFHEAL=1 to start it. (Desired-state reporting stays report-only;
# this watcher is a separate, opt-in operational aid.)
if [ "${MXL_SELFHEAL:-}" = 1 ] && [ -f "$REPO/tools/mxl-selfheal.sh" ]; then
  step "Self-healer"
  pkill -f "mxl-selfheal.sh" 2>/dev/null || true
  MXL_FACILITY_JSON="${GEN_FACILITY:-$REPO/config/facility.json}" \
    nohup bash "$REPO/tools/mxl-selfheal.sh" --watch >/tmp/mxl-selfheal.log 2>&1 &
  echo "  ✓ self-healer watching (selector + relay) — log: /tmp/mxl-selfheal.log"
fi

# ── 4. done ───────────────────────────────────────────────────────────────────
sleep 4
GUEST_PASS_NOTE=""
[ -n "$GUEST_CONF" ] && [ "$GUEST_TRANSPORT" != srt-listen ] && GUEST_PASS_NOTE="         Passphrase: the SRT passphrase you configured for that slot (encryption AES, required)"
# Transport-aware "put your face on air" help: srt-listen has per-guest ports + no
# streamid; srt-direct/rtsp share 8890 with a publish:guestN streamid.
if [ "$GUEST_TRANSPORT" = srt-listen ]; then
  _g2p="$((GUEST_LISTEN_BASE_PORT + 1))"
  GUEST_SRT_HELP="         URL:  srt://$PUBLIC_IP:$GUEST_LISTEN_BASE_PORT  (Guest 1)   ·   srt://$PUBLIC_IP:$_g2p  (Guest 2)
         Mode: Caller   ·   Stream ID: (leave blank — the port picks the guest)"
  GUEST_SRT_FW="(OBS/vMix/ffmpeg work too — same URL. Open UDP $GUEST_LISTEN_BASE_PORT AND $_g2p in your cloud firewall.)"
else
  GUEST_SRT_HELP="         URL:  srt://$PUBLIC_IP:8890
         Mode: Caller   ·   Stream ID:  publish:guest1   (or publish:guest2)"
  GUEST_SRT_FW="(OBS/vMix/ffmpeg work too — same URL. Open 8890/udp in your cloud firewall.)"
fi
cat <<EOF

✅ YOUR MXL SWITCHER IS ON AIR
   Watch the program:   http://$PUBLIC_IP:8889/mxl2webrtc/
   (black video? open 8889/tcp AND 8189/udp in your cloud firewall)
${CONTROL_UI_URL:+
   🎛  Drive it in a browser: $CONTROL_UI_URL
      (localhost-only — from your laptop:  ssh -L ${MXL_CONTROL_PORT:-3100}:127.0.0.1:${MXL_CONTROL_PORT:-3100} user@$PUBLIC_IP  then open the URL)
}

   CUT to the clip:     curl -X POST -H 'Content-Type: application/json' -d '{"slot":1}' http://127.0.0.1:9604/pipeline/active-input
   CUT to the pattern:  curl -X POST -H 'Content-Type: application/json' -d '{"slot":0}' http://127.0.0.1:9604/pipeline/active-input
   Change the pattern:  curl -X POST -H 'Content-Type: application/json' -d '{"pattern":"Pinwheel"}' http://127.0.0.1:9600/video/test-pattern
   Graphics off/on:     curl -X POST -H 'Content-Type: application/json' -d '{"on":false}' http://127.0.0.1:9605/pipeline/key

   📱 PUT YOUR OWN FACE ON AIR (no camera needed):
      Install "Larix Broadcaster" (free, iOS/Android). New connection → SRT →
$GUEST_SRT_HELP
$GUEST_PASS_NOTE
      Tap to go live → it appears as Guest 1, cuttable like any source:
         curl -X POST -H 'Content-Type: application/json' -d '{"slot":2}' http://127.0.0.1:9604/pipeline/active-input
      $GUEST_SRT_FW

   Every pixel above crossed a shared-memory MXL domain at $DOMAIN_HOST
   List the flows:      docker exec input-selector /opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l

   NEXT STEPS (docs/QUICKSTART.md): add your own camera over SRT, thumbnails,
   layouts, a multiview wall, guest contribution — tools/ has every piece,
   scripts/bring-up-mxl.sh shows the full assembly, docs/FINDINGS.md saves
   you the outages. Teardown: sudo $0 --down
EOF
