#!/bin/bash
# The ffmpeg.wasm core binary is 32MB — not committed to keep the repo lean.
# Run this once from web/vendor/ffmpeg/ to pull the exact matching build.
set -e
cd "$(dirname "$0")"
URL="https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/umd/ffmpeg-core.wasm"
echo "Fetching ffmpeg-core.wasm (32MB) from $URL ..."
curl -fL -o ffmpeg-core.wasm "$URL"
echo "Done. Size: $(du -h ffmpeg-core.wasm | cut -f1)"
