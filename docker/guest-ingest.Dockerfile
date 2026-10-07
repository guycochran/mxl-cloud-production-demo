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
# gstreamer1.0-libav adds it. The DEFAULT guest transport is now SRT-direct
# (srtsrc!tsdemux!h264parse/aacparse!avdec_*) — tsdemux (bad) + aacparse (good) +
# avdec_aac (libav) are all present with the base + libav below.
#
# Build (run from repo root):
#   docker build -f docker/guest-ingest.Dockerfile -t mxl-guest-ingest:local .
# Run (quickstart wires this): one container per guest slot, SRT in → MXL flow.

# Base pinned by digest for reproducibility (docs/VERSIONS.md). This digest bakes
# in MXL SDK v1.1.0 (released 2026-09-09; Flow + Fabric API) — the gst-mxl-rs
# contract contribution_core._restamp relies on (mxlsink maps pts->grain index via
# one shared clock offset D and has NO backward-index guard, so the writer requires
# monotonic PTS from us). Verify with the commands in docs/VERSIONS.md before a
# cold-clone proof; a base refresh could silently change the SDK version.
# Override to track upstream:  docker build --build-arg BASE=ghcr.io/cbcrc/test-generator:latest ...
ARG BASE=ghcr.io/cbcrc/test-generator@sha256:09cad0981475095ab948ca51511d4fbdc0521e2a23632d50abaf14fc3847cd92
FROM ${BASE}

USER root
# the only missing piece: software H.264 decode for contribution ingest
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends gstreamer1.0-libav \
 && rm -rf /var/lib/apt/lists/*

# OPT-IN: closed-caption / ANC (ST-2038) data-flow support for CaptionFileAdapter
# (Tier 3.1, docs/ROADMAP-FROM-SPECS-2026-10.md). Needs `ccconverter`
# (gstreamer1.0-plugins-bad) + the `rsclosedcaption` plugin (cctost2038anc /
# tttocea608) from gst-plugins-rs >= 0.14, which the stock cbcrc base lacks. OFF by
# default so the HW-proven image is byte-identical; enable for a caption build with:
#   docker build --build-arg WITH_CAPTIONS=1 -f docker/guest-ingest.Dockerfile ...
# NOTE: rsclosedcaption is not in Ubuntu apt; the upstream devcontainer builds it from
# source (rust/gst-mxl-rs/README.md). This arg installs plugins-bad and documents the
# remaining gst-plugins-rs source build — it is NOT yet HW-verified end to end.
ARG WITH_CAPTIONS=0
RUN if [ "$WITH_CAPTIONS" = "1" ]; then \
      apt-get update -qq \
      && apt-get install -y -qq --no-install-recommends gstreamer1.0-plugins-bad \
      && rm -rf /var/lib/apt/lists/* ; \
      echo "WITH_CAPTIONS=1: plugins-bad (ccconverter) installed; rsclosedcaption (gst-plugins-rs >=0.14) must still be built from source — see rust/gst-mxl-rs/README.md" ; \
    fi

# the contribution seam (core + adapters + entrypoints, flat so imports resolve from /opt/seam)
# guest_audio.py = the A/V guest's AUDIO leg (v0.3); facility.py lets both legs resolve
# flow UUIDs from config/facility.json (copied below) with the baked-in fallback.
WORKDIR /opt/seam
COPY tools/contribution_core.py tools/adapters.py tools/facility.py \
     tools/guest_ingest.py tools/guest_audio.py tools/guest_av_listen.sh \
     tools/zoomiso_dryrun.py ./
# facility manifest so facility.py resolves it from the cwd (see tools/facility.py search path)
COPY config/facility.json ./config/facility.json

# guest_ingest.py args: <srt-stream-name> <flow-uuid> <label> [jitterbuffer_ms]
# The quickstart passes a cellular-friendly jitterbuffer (SRT latency is per-path
# physics — ~1000ms for a phone, FINDINGS §9). Default below is the wired value;
# the quickstart overrides it per guest. REPAIR_URL is unset here → the adapter's
# announce falls back to the local selector re-attach the quickstart wires.
ENTRYPOINT ["python3", "guest_ingest.py"]
