#!/bin/bash
# ensure-single-writers.sh — make each single-instance hls2mxl pipeline have
# EXACTLY ONE supervisor+child. Fixes the duplicate-writer hazard that the
# pipelines section's self-matching `pkill -f run-camN.sh` can create when
# bring-up runs on an already-running box (the kill's own shell cmdline
# contains the pattern, so pkill kills itself before finishing → orphans).
# Run on the VM HOST (needs bash for [x]-bracket patterns; container is dash).
# Usage: ensure-single-writers.sh            (dedup only; won't start missing)
#        ensure-single-writers.sh --start     (also start any at count 0)
set -u
START=${1:-}
D(){ sudo docker exec hls2mxl sh -c "$1"; }
# py-basename : run-script  (guest_* excluded — they legitimately run 0-2)
for pair in cam_ingest:run-cam1.sh cam2_ingest:run-cam2.sh \
            layout_pgm:run-layout.sh audio_pgm:run-audio.sh mxl_thumbs:run-thumbs.sh; do
  py=${pair%%:*}; run=${pair##*:}
  pb="[${py:0:1}]${py:1}"; rb="[${run:0:1}]${run:1}"
  n=$(D "pgrep -fc '$pb'" 2>/dev/null || echo 0); n=${n:-0}
  if [ "$n" -gt 1 ]; then
    echo "  $py x$n — collapsing to 1"
    D "pkill -9 -f '$rb'; pkill -9 -f '$pb'; sleep 2"
    sudo docker exec -d hls2mxl /tmp/$run
  elif [ "$n" -eq 0 ] && [ "$START" = "--start" ]; then
    echo "  $py x0 — starting"
    sudo docker exec -d hls2mxl /tmp/$run
  else
    echo "  $py x$n ✓"
  fi
done
