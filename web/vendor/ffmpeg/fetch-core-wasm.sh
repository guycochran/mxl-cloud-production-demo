#!/bin/bash
# The ffmpeg.wasm core binary is 32MB — not committed to keep the repo lean.
# Run this once from web/vendor/ffmpeg/ to pull the exact matching build.
# LICENSE: this binary is FFmpeg built with --enable-gpl + libx264/libx265, i.e.
# GPL-2.0-or-later. Serving it to browsers distributes GPL object code; see
# THIRD-PARTY-NOTICES.md at the repo root for what that means.
set -e
cd "$(dirname "$0")"
URL="https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/umd/ffmpeg-core.wasm"
echo "Fetching ffmpeg-core.wasm (32MB) from $URL ..."
curl -fL -o ffmpeg-core.wasm "$URL"
echo "Done. Size: $(du -h ffmpeg-core.wasm | cut -f1)"
echo "Note: ffmpeg-core.wasm is GPL-2.0-or-later (FFmpeg --enable-gpl, x264/x265). See THIRD-PARTY-NOTICES.md."
