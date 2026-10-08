<!-- SPDX-License-Identifier: Apache-2.0 -->
# Third-party notices

The project's own code is licensed under Apache-2.0 (see [LICENSE](LICENSE)). The
components below are bundled in, or fetched by, this repository and remain under their
own licenses.

## ffmpeg.wasm — `web/vendor/ffmpeg/`

Used by the in-browser clip/record pages (`web/mxl-clip.html`, `web/mxl-tams.html`).

| File | Upstream package | Committed? | License |
|---|---|---|---|
| `ffmpeg.js`, `814.ffmpeg.js` | `@ffmpeg/ffmpeg` **0.12.10** (byte-identical to the npm `dist/umd` files) | yes | MIT |
| `ffmpeg-core.js` | `@ffmpeg/core` **0.12.6** Emscripten loader (byte-identical to npm `dist/umd/ffmpeg-core.js`) | yes | MIT (per the npm package metadata) |
| `ffmpeg-core.wasm` | `@ffmpeg/core` **0.12.6** compiled FFmpeg | **no** — fetched by `fetch-core-wasm.sh`, git-ignored | **GPL-2.0-or-later** (see below) |

Upstream: <https://github.com/ffmpegwasm/ffmpeg.wasm>. MIT License text for the
ffmpeg.wasm JavaScript:

```
MIT License

Copyright (c) 2019 Jerome Wu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### The core WebAssembly binary is GPL

Although the npm package metadata for `@ffmpeg/core` says "MIT", the compiled
`ffmpeg-core.wasm` it ships is FFmpeg **5.1** (libavcodec 59.37) configured with `--enable-gpl` and linked
with GPL libraries (libx264, libx265), plus libvpx, LAME, Theora, Vorbis, Opus, zlib,
libwebp, FreeType, FriBidi, libass and zimg. FFmpeg's own build configuration embedded in
the binary reads (excerpt):

```
--enable-gpl --enable-libx264 --enable-libx265 --enable-libvpx --enable-libmp3lame
--enable-libtheora --enable-libvorbis --enable-libopus --enable-zlib --enable-libwebp
--enable-libfreetype --enable-libfribidi --enable-libass --enable-libzimg
```

So the **combined binary is licensed GPL-2.0-or-later** (FFmpeg's `--enable-gpl` mode,
x264 and x265 are GPL-2.0-or-later).

What that means here:

- The `.wasm` is **not committed** to this repository; `fetch-core-wasm.sh` downloads the
  exact upstream build on demand. No GPL object code is distributed in this Git tree.
- **If you deploy the clip/record pages, your web server distributes that GPL binary to
  browsers.** You are then responsible for GPL compliance for it (for example, pointing
  users to the corresponding source: the ffmpeg.wasm `v0.12.6` core build scripts at
  <https://github.com/ffmpegwasm/ffmpeg.wasm>, FFmpeg 5.1, x264 and x265 sources).
- If GPL obligations aren't acceptable for your deployment, build an LGPL-only core
  (no `--enable-gpl`, no x264/x265) and serve that instead, or don't deploy those pages.

## Web fonts — `web/fonts/`

Barlow Condensed and IBM Plex Mono, under the SIL Open Font License 1.1. See
[`web/fonts/LICENSE`](web/fonts/LICENSE).

## Runtime dependencies (not bundled)

- `backend/` installs `express` and its dependencies from npm (`backend/package-lock.json`),
  each under its own license (MIT for express).
- The quickstart pulls container images (e.g. `ghcr.io/cbcrc/*`, `bluenviron/mediamtx`) at
  run time; they carry their own licenses and are not redistributed by this repository.
