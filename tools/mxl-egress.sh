#!/bin/bash
# mxl-egress.sh — OUTPUT NODE for the MXL demo (the "distribute" pillar, akin to
# a Grass Valley AMPP output node). Pulls the finished PROGRAM that mediamtx
# already carries (path mxl2webrtc) and pushes it to an RTMP/SRT destination.
# Video is copied (already H.264); audio is transcoded Opus->AAC (YouTube RTMP
# requires AAC). Multiple destinations = multiple concurrent pushers, each with
# its own pidfile. Run on VM1 (localhost mediamtx source, fast Azure uplink).
#
#   mxl-egress.sh start youtube                  # uses server-side ~/.mxl-stream-key
#   mxl-egress.sh start custom rtmp://host/app KEY
#   mxl-egress.sh stop  <youtube|custom>
#   mxl-egress.sh status
set -u
SRC="rtsp://127.0.0.1:8554/mxl2webrtc"
KEYFILE="$HOME/.mxl-stream-key"
RUNDIR="/tmp/mxl-egress"; mkdir -p "$RUNDIR"
cmd="${1:-status}"; name="${2:-}"

pidfile(){ echo "$RUNDIR/$1.pid"; }
logfile(){ echo "$RUNDIR/$1.log"; }
alive(){ local p; p=$(cat "$(pidfile "$1")" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

push(){  # $1=name  $2=full rtmp/srt url
  local nm="$1" url="$2"
  if alive "$nm"; then echo "already streaming: $nm"; return 0; fi
  # -c:v copy (program is H.264) ; -c:a aac (YouTube needs AAC, source is Opus)
  # flvflags no_duration_filesize + realtime pacing; auto-reconnect on blips.
  nohup ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp \
    -fflags +genpts -i "$SRC" \
    -c:v copy -c:a aac -b:a 160k -ar 44100 \
    -f flv "$url" >> "$(logfile "$nm")" 2>&1 &
  echo $! > "$(pidfile "$nm")"
  sleep 2
  if alive "$nm"; then echo "streaming: $nm (pid $(cat "$(pidfile "$nm")"))"; else
    echo "FAILED to start $nm — tail:"; tail -3 "$(logfile "$nm")"; return 1; fi
}

case "$cmd" in
  start)
    case "$name" in
      youtube)
        [ -s "$KEYFILE" ] || { echo "no key file"; exit 1; }
        push youtube "$(cat "$KEYFILE")" ;;
      custom)
        base="${3:-}"; key="${4:-}"
        [ -n "$base" ] || { echo "custom needs a URL"; exit 1; }
        # accept "rtmp://host/app KEY" or a full URL in $base
        [ -n "$key" ] && url="${base%/}/$key" || url="$base"
        push custom "$url" ;;
      *) echo "usage: start youtube | start custom <url> [key]"; exit 1 ;;
    esac ;;
  stop)
    [ -n "$name" ] || { echo "usage: stop <name>"; exit 1; }
    p=$(cat "$(pidfile "$name")" 2>/dev/null)
    if [ -n "$p" ]; then kill "$p" 2>/dev/null; sleep 1; kill -9 "$p" 2>/dev/null; fi
    rm -f "$(pidfile "$name")"; echo "stopped: $name" ;;
  status)
    any=0
    for pf in "$RUNDIR"/*.pid; do
      [ -e "$pf" ] || continue
      nm=$(basename "$pf" .pid)
      if alive "$nm"; then echo "$nm: LIVE (pid $(cat "$pf"))"; any=1
      else echo "$nm: dead"; rm -f "$pf"; fi
    done
    [ "$any" = 0 ] && echo "no active egress" ;;
  *) echo "usage: {start|stop|status}"; exit 1 ;;
esac
