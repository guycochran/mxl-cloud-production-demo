#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# guest_av_listen — A/V SRT fan-out for srt-listen mode.
#
# In srt-listen mode the contributor dials ONE SRT stream straight into the switcher
# (no mediamtx). That one MPEG-TS carries BOTH video (H.264) and audio (AAC). A single
# srtsrc listener can only feed one pipeline, and the guest video + audio ingests are
# separate ContributionCore processes (one per essence, by design) — so we SPLIT the
# one public stream into two local streams, one per essence:
#
#   contributor ──SRT──▶ :PUBLIC_PORT  (srtsrc listener, THIS script)
#                           │ tsparse ! tee
#                           ├─▶ srtsink caller ▶ 127.0.0.1:VIDEO_PORT  (video core listens)
#                           └─▶ srtsink caller ▶ 127.0.0.1:AUDIO_PORT  (audio core listens)
#
# The video core (guest_ingest.py srt-listen) and audio core (guest_audio.py srt-listen)
# each run a srtsrc LISTENER bound to 127.0.0.1 on their own local port; this fan-out
# dials them as a CALLER. Both legs get the full TS and tsdemux taps their own essence.
# Zero changes to contribution_core.py — the two cores run exactly as they do for a
# single-port listen; the fan-out just gives them a stream to read.
#
# Why not two public ports (one per essence)? The contributor is ONE SRT sender; it
# can't publish the same stream to two sockets. The split must happen server-side.
#
# Usage: guest_av_listen.sh <public_port> <video_port> <audio_port> [latency_ms]
# POSIX sh-safe (the container entrypoint may be dash): no `pipefail`, no bashisms.
set -eu

PUBLIC_PORT="${1:?public SRT port}"
VIDEO_PORT="${2:?local video leg port}"
AUDIO_PORT="${3:?local audio leg port}"
LATENCY_MS="${4:-300}"

# Supervised loop: if the contributor disconnects (EOS) or a leg restarts, gst-launch
# exits and we rebuild. The leg listeners (video/audio cores) are supervised separately;
# our srtsink callers reconnect to them. wait-for-connection=false so the pipeline comes
# up and listens even before a contributor or a leg is present.
while :; do
  gst-launch-1.0 -e \
    srtsrc uri="srt://0.0.0.0:${PUBLIC_PORT}?mode=listener&latency=${LATENCY_MS}" \
      ! tsparse set-timestamps=true ! tee name=t \
    t. ! queue ! srtsink uri="srt://127.0.0.1:${VIDEO_PORT}?mode=caller&latency=${LATENCY_MS}" \
                         wait-for-connection=false \
    t. ! queue ! srtsink uri="srt://127.0.0.1:${AUDIO_PORT}?mode=caller&latency=${LATENCY_MS}" \
                         wait-for-connection=false \
    || true
  echo "guest_av_listen: fan-out exited (contributor gone or leg churn) — rebuilding" >&2
  sleep 2
done
