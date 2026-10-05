# Quickstart: your own MXL switcher in minutes

*Cold-clone verified Oct 2026 on fresh VMs across three clouds — git clone →
`quickstart.sh` → ON AIR → one curl cut landed (HTTP 200): **GCP 2m33s · AWS
3m59s · Azure 4m17s**, each exit 0, then deleted (cents). Timing is dominated
by the one-time container-image pull; subsequent runs are faster.*

One script takes a fresh Ubuntu VM to a **cuttable, keyed, browser-watchable
MXL production** with **open phone/SRT contribution built in**: test pattern +
file playout through a shared-memory domain, an HTML5 lower-third keyed over
program, two SRT guest slots you can put a phone on, and WebRTC out. Everything
runs in the stock [cbcrc/mxl-hands-on](https://github.com/cbcrc/mxl-hands-on)
containers — this script is just verified assembly. Container images are
**pinned by digest** for reproducibility ([VERSIONS.md](VERSIONS.md); opt into
upstream with `MXL_BLEEDING_EDGE=1`).

## Prerequisites

- **Ubuntu 22.04/24.04, x86-64 with AVX.** Any Azure D-series v5, AWS m5/m6i,
  GCP n2. On QEMU/KVM set the CPU model to `host` or every libmxl call SIGILLs
  ([FINDINGS §11](FINDINGS.md)). Check: `grep -m1 avx /proc/cpuinfo`.
- **~8 vCPU / 16 GB** is comfortable for this baseline (a 4-vCPU D4s_v5 runs
  it; measured sizing for the full facility is in the README's *Build one
  yourself* table). **~10 GB free disk** — the container images total ~7 GB
  (the CEF-based keyer alone is 3.3 GB), so the image pull dominates the run
  time.
- **Cloud firewall / NSG open:** `8889/tcp` (WebRTC page + signaling),
  `8189/udp` (WebRTC media), and `8890/udp` (SRT guest contribution — the phone
  slots are now part of the quickstart, so open this too). The unauthenticated
  media-function **control APIs (`9600`–`9605`) bind to `127.0.0.1` only** — drive
  them from the box (`curl 127.0.0.1:…`) or over an SSH tunnel; never expose them.

## Run it

```bash
git clone https://github.com/guycochran/mxl-switcher
cd mxl-switcher
sudo scripts/quickstart.sh
```

A few minutes, mostly image pulls and a sample-clip download. It's idempotent —
re-run any time (also after a reboot: the domain lives in `/dev/shm` and is
deliberately volatile). Teardown: `sudo scripts/quickstart.sh --down`.

What it does, in order: preflight (AVX, docker) → sample clip (+ a one-time
1080p30 conform if ffmpeg is present) → a self-contained lower-third page on
`:8085` → six containers with the exact wiring of the live
[mxlswitcher.com](https://mxlswitcher.com) deployment → starts the writers →
**discovers their flow UUIDs at runtime** with `mxl-info` → selector → keyer →
encoder → **two SRT guest slots + a backend-free watcher** that attaches a
phone the moment it connects. The final banner prints your viewer URL, the
guest SRT publish point, and the control one-liners.

### Check its health

```bash
sudo scripts/mxl-doctor          # one-glance: containers, flow presence, program path
sudo scripts/mxl-doctor --watch  # refresh every 3s
sudo scripts/mxl-doctor --deep   # + true unique-frame liveness (starts a transient grain probe)
```

`mxl-doctor` is the single front door for lab health. The default report is
read-only and backend-free. Add `--deep` and it reports not just "a flow exists"
but whether it's carrying **fresh** media (unique frames/sec) — the signal that
catches a repeat-wedged reader a plain frame-rate check misses. (It's opt-in
because the liveness probe attaches a reader per flow and pushes CPU.)

The default still works as `sudo scripts/doctor.sh`; `mxl-doctor` wraps it plus
the live-facility auto-healers (`mxl-doctor heal selector|program|guest`).

## Drive it

```bash
# what's in the domain (every UUID is a live shared-memory flow)
docker exec input-selector /opt/mxl/tools/mxl-info/mxl-info -d /mxl-domain -l

# CUT — slot 0 = pattern, slot 1 = clip (~1 frame, graphics stay up)
curl -X POST -H 'Content-Type: application/json' -d '{"slot":1}' http://127.0.0.1:9604/pipeline/active-input

# patterns, key on/off
curl -X POST -H 'Content-Type: application/json' -d '{"pattern":"Pinwheel"}' http://127.0.0.1:9600/video/test-pattern
curl -X POST -H 'Content-Type: application/json' -d '{"on":false}'           http://127.0.0.1:9605/pipeline/key
```

Watch at `http://<your-ip>:8889/mxl2webrtc/`. Edit
`/srv/mxl-quickstart/graphics/lower-third.html` and re-toggle the key to see
your own graphics keyed in-cloud (CEF caches — add `?v=2` to the `html5_url`
if an edit doesn't show: FINDINGS has the details).

### Put your phone on air

The quickstart arms two SRT guest slots. Point any SRT encoder — the free
**Larix Broadcaster** app, OBS, or vMix — at the publish point from the banner:

```
srt://<your-ip>:8890    streamid:  publish:guest1   (or publish:guest2)
```

The watcher attaches the slot automatically the moment the stream connects; then
cut to it:

```bash
# slot 2 = Guest 1, slot 3 = Guest 2
curl -X POST -H 'Content-Type: application/json' -d '{"slot":2}' http://127.0.0.1:9604/pipeline/active-input
```

Larix note: free Larix needs the stream id as its dedicated `srtstreamid` field
(not baked in the URL). The banner prints a scannable QR with the exact scheme.

**Slot numbers are stable.** Guest 1 is always slot 2 and Guest 2 always slot 3 —
whether or not the other is streaming. An absent guest's slot is held by a safe
source (Pattern) until its phone connects, so a control surface / Companion / an
operator can trust button numbers and a live cut never gets renumbered under you.
The current mapping is published at `/tmp/mxl-slot-map.json`
(`{"0":"Pattern Video","2":"Guest 1 (idle)", …}`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `illegal instruction` in any container log | No AVX. QEMU: CPU model `host`. (§11) |
| Viewer page loads, video black | `8189/udp` closed, or `MTX_WEBRTCADDITIONALHOSTS` didn't get your public IP (re-run the script; it re-detects). |
| Selector start returns 400 `flow_def.json not found` | A writer died before the selector attached — `docker logs test-generator file-player`, then re-run. |
| Cut works but shows a frozen/old frame | Reader wedged on a recreated flow — the fundamental MXL gotcha. Restart the selector pipeline (stop + start with the same body). §6. |
| Clip stutters on cuts | Non-30fps source vs the 30/1 domain. Install ffmpeg and re-run (it conforms the clip), or bring a 1080p30 file. §8. |

## Where to go next

Phone/SRT guest contribution is already running (above). The next functions each
add one tool from [`tools/`](../tools/);
[`scripts/bring-up-mxl.sh`](../scripts/bring-up-mxl.sh) shows them all assembled
(it's the live demo's actual cold-start):

1. **A studio camera as a cuttable input** — point an RTSP/SRT camera at the box;
   the same contribution seam that drives the guest slots handles it
   ([`tools/adapters.py`](../tools/adapters.py) `RtspCamAdapter`;
   [CONTRIBUTION-SPLIT.md](CONTRIBUTION-SPLIT.md)). The cadence-preserving
   re-stamp in [`contribution_core.py`](../tools/contribution_core.py) is *the*
   trick that makes a remote source cut cleanly (FINDINGS §1).
2. **Guest audio / audio-follow-video** — [`tools/guest_audio.py`](../tools/guest_audio.py),
   [`tools/audio_pgm.py`](../tools/audio_pgm.py).
3. **Preview thumbnails** — [`tools/mxl_thumbs.py`](../tools/mxl_thumbs.py).
4. **Layouts as an input** — [`tools/layout_pgm.py`](../tools/layout_pgm.py).
5. **A multiview wall, hardware-style** — [`tools/mxl_multiview.py`](../tools/mxl_multiview.py)
   + [`tools/mv_encode.py`](../tools/mv_encode.py) (FINDINGS §10 before you
   "clean up" either).
6. **Record into TAMS / cross-host fabric** — [TAMS.md](TAMS.md),
   [JONAS-FABRIC-HANDOFF.md](JONAS-FABRIC-HANDOFF.md).

Read [FINDINGS.md](FINDINGS.md) *before* you debug anything. Every numbered
section is an outage we already had so you don't have to.

**Want the concepts, not just the facility?** The upstream
[cbcrc/mxl-hands-on Exercises](https://github.com/cbcrc/mxl-hands-on/tree/main/Exercises)
(single writer → multiple domains → GUI probing → "full open-source DMF") are
the guided-learning track; this quickstart is the everything-running-now
track. They compose well: do Exercise 1 once and the flow list above stops
being magic.
