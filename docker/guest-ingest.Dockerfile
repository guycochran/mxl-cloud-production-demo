# guest-ingest — SRT contributor → MXL guest flow, as a container.
#
# The quickstart host is bare Ubuntu+docker (no GStreamer/mxl toolchain on the
# host, on purpose). This image gives the contribution seam a home: it layers
# our contribution_core.py + adapters.py on top of a stock cbcrc media-function
# image that ALREADY ships GStreamer 1.24 + the MXL gst plugins (mxlsink/mxlsrc)
# — so we get the proven mxl environment for free and only add the one thing the
# cbcrc images lack: a software H.264 decoder for compressed contribution ingest.
#
# Verified on the base image 2026-10-02: gst-inspect finds mxlsink/mxlsrc, gi +
# GStreamer 1.24.2 import, rtspsrc/srtsrc/rtph264depay/h264parse present;
# avdec_h264 is NOT (cbcrc functions handle decoded v210, not compressed in) →
# gstreamer1.0-libav adds it.
#
# Build (run from repo root):
#   docker build -f docker/guest-ingest.Dockerfile -t mxl-guest-ingest:local .
# Run (quickstart wires this): one container per guest slot, SRT in → MXL flow.

FROM ghcr.io/cbcrc/test-generator:latest

USER root
# the only missing piece: software H.264 decode for contribution ingest
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends gstreamer1.0-libav \
 && rm -rf /var/lib/apt/lists/*

# the contribution seam (core + adapters, flat so imports resolve from /opt/seam)
WORKDIR /opt/seam
COPY tools/contribution_core.py tools/adapters.py tools/guest_ingest.py ./

# guest_ingest.py args: <srt-stream-name> <flow-uuid> <label> [jitterbuffer_ms]
# The quickstart passes a cellular-friendly jitterbuffer (SRT latency is per-path
# physics — ~1000ms for a phone, FINDINGS §9). Default below is the wired value;
# the quickstart overrides it per guest. REPAIR_URL is unset here → the adapter's
# announce falls back to the local selector re-attach the quickstart wires.
ENTRYPOINT ["python3", "guest_ingest.py"]
