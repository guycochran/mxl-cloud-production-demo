# MXL Cloud Production Demo

**A complete live broadcast production — real PTZ camera, file playout, test patterns,
graphics keyer, program audio — running through an [EBU MXL](https://github.com/dmf-mxl/dmf-mxl)
shared-memory domain on a single ~$0.38/hr cloud VM, controllable by anyone with a browser.**

Built by [Office Hours Global](https://officehours.global) ahead of IBC 2026 to show that
the Dynamic Media Facility vision isn't just for broadcasters with NVIDIA partnerships —
one person can stand up cloud shared-memory production in a weekend with the open tooling.

![Live camera through MXL](docs/images/demo-camera.png)
*Live PTZ camera (US studio) → SRT → MXL domain in Azure → HTML5 graphics keyed in-cloud →
WebRTC to the browser. The visitor pans the real camera from the right-hand console.*

![Real episode playout](docs/images/demo-playout.png)
*One click later: real episode playout through the same shared-memory chain, audio following the cut.*

## What it demonstrates

- **Remote camera as a first-class MXL source.** RTSP/SRT contribution lands in the domain
  with its grain index aligned to locally-generated flows, so a remote camera is an
  *instantly-cuttable* selector input (~30 ms cuts, graphics stay up). See
  [`tools/cam_ingest.py`](tools/cam_ingest.py) and the timing discussion in
  [`docs/FINDINGS.md`](docs/FINDINGS.md).
- **Audio-follow-video** assembled inside the domain by a fourth writer
  ([`tools/audio_pgm.py`](tools/audio_pgm.py)): episode audio on playout, bars-and-tone on
  pattern, silence on camera.
- **Sub-second glass-to-cloud-graphics latency**, proven by the camera's burnt-in clock and
  the cloud-keyed clock reading the same second in a single frame.
- **The DMF white paper's use cases in miniature**: "minimise small-site footprint",
  "shipping compute", "outsourcing peaks", and (deallocate when idle) "supporting
  sustainability" — on a general-purpose VM with zero specialised hardware.

## Architecture

```
US studio                              Azure VM (D8s_v5, Ubuntu 24.04)
─────────                              ────────────────────────────────────────────
PTZ camera ──RTSP──► ffmpeg ──SRT──►   mediamtx ──RTSP──► cam_ingest.py ─┐
             (re-encode 1080p30)                     (PTS re-stamped     │ /dev/shm
                                                      to "now"+margin)   ▼
                                       test-generator ──────────► ┌──────────────┐
                                       file-player (episode) ───► │  MXL domain   │
                                       audio_pgm.py (PGM audio) ► │ (shared mem)  │
                                                                  └──────┬────────┘
                                       input-selector ◄── reads ─────────┤
                                       html5-keyer (CEF, lower-third) ◄──┤
                                       mxl2webrtc + mediamtx ◄───────────┘
                                              │
Browser ◄─────────── WebRTC ◄─────────────────┘   (kiosk page cuts program via
                                                   small Express proxy routes)
```

Runtime apps are the stock **[cbcrc/mxl-hands-on](https://github.com/cbcrc/mxl-hands-on)**
containers (test generator, file player, input selector, HTML5 keyer, mxl2webrtc),
orchestrated with **[CLOUDflex-broadcast/easy-mxl](https://github.com/CLOUDflex-broadcast/easy-mxl)**.
This repo adds the glue that made it a *usable remote production*:

| Piece | What it does |
|---|---|
| [`tools/cam_ingest.py`](tools/cam_ingest.py) | Low-latency RTSP→MXL ingest. 150 ms jitterbuffer, decode to v210, cadence-preserving PTS re-stamp so the remote camera's grain index aligns with local flows. **This is what makes cross-flow cutting of a remote source work.** |
| [`tools/audio_pgm.py`](tools/audio_pgm.py) | Audio-follow-video: GStreamer `input-selector` over silence / clip audio / tone, following the video selector's status API. Writes a "PGM Audio" flow the encoder reads. |
| [`tools/cam_relay.py`](tools/cam_relay.py) | The first-generation fixed-offset latency normalizer (superseded by `cam_ingest.py`, kept for the record — see FINDINGS). |
| [`backend/mxl-routes.js`](backend/mxl-routes.js) | Express routes proxying browser clicks to the pipeline APIs (cut / key / pattern / one-call cascade repair). |
| [`web/mxl.html`](web/mxl.html) | The kiosk page: WebRTC program feed + camera-control console + switcher bar. |
| [`web/lower-third.html`](web/lower-third.html) | Transparent OGraf-style lower-third + live clock + bug, rendered by the CBC HTML5 keyer's CEF. |
| [`scripts/bring-up-mxl.sh`](scripts/bring-up-mxl.sh) | One command from cold VM to running demo: containers → writers → ingest → selector → keyer → encoder → tunnel → page. |

## The hard-won lessons

The interesting engineering is in **[docs/FINDINGS.md](docs/FINDINGS.md)** — including:

1. Why a remote source stutters when cut in a grain-indexed shared-memory domain,
   and the cadence-preserving re-stamp that fixes it.
2. A measured case of the white paper's open topic *"robustness when Grains are missing"*
   (single-frame content flashes from stale ring slots) and the write-ahead margin that
   prevents it.
3. The GStreamer `rtspsrc` default 2000 ms jitterbuffer hiding inside `uridecodebin` —
   where almost all our latency was.
4. `mxlsink` requires an explicit `flow-id`; an empty one fails as a misleading
   `not-negotiated (-4)`.
5. Reader wedge on flow recreation, and the cascade-repair pattern that recovers.

## Credits

- EBU DMF / MXL community — the [MXL SDK](https://github.com/dmf-mxl/dmf-mxl) and the
  Dynamic Media Facility white paper.
- CBC/Radio-Canada — the [mxl-hands-on](https://github.com/cbcrc/mxl-hands-on) media
  function containers.
- [CLOUDflex-broadcast/easy-mxl](https://github.com/CLOUDflex-broadcast/easy-mxl) — the
  control panel that makes the domain approachable.

MIT licensed. Not affiliated with EBU or CBC; all findings offered upstream with love.
