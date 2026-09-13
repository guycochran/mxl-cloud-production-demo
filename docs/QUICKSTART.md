# Quickstart: your own MXL switcher in ~10 minutes

*Validated end-to-end 2026-09-13 on a fresh Azure D4s_v5 (Ubuntu 24.04):
script → ON AIR → browser showed bars with the keyed lower-third → one curl
cut to the clip, graphics stayed up — then the VM was deleted. Total cost of
the test: about five cents.*

One script takes a fresh Ubuntu VM to a **cuttable, keyed, browser-watchable
MXL production**: test pattern + file playout through a shared-memory domain,
an HTML5 lower-third keyed over program, and WebRTC out. Everything runs in
the stock [cbcrc/mxl-hands-on](https://github.com/cbcrc/mxl-hands-on)
containers — this script is just verified assembly.

## Prerequisites

- **Ubuntu 22.04/24.04, x86-64 with AVX.** Any Azure D-series v5, AWS m5/m6i,
  GCP n2. On QEMU/KVM set the CPU model to `host` or every libmxl call SIGILLs
  ([FINDINGS §11](FINDINGS.md)). Check: `grep -m1 avx /proc/cpuinfo`.
- **~8 vCPU / 16 GB** is comfortable for this baseline (a 4-vCPU D4s_v5 runs
  it; measured sizing for the full facility is in the README's *Build one
  yourself* table). **~10 GB free disk** — the container images total ~7 GB
  (the CEF-based keyer alone is 3.3 GB), so the image pull dominates the 10
  minutes.
- **Cloud firewall / NSG open:** `8889/tcp` (WebRTC page + signaling),
  `8189/udp` (WebRTC media). Optional for later: `8890/udp` (SRT contribution).

## Run it

```bash
git clone https://github.com/guycochran/mxl-cloud-production-demo
cd mxl-cloud-production-demo
sudo scripts/quickstart.sh
```

~10 minutes, mostly image pulls and a sample-clip download. It's idempotent —
re-run any time (also after a reboot: the domain lives in `/dev/shm` and is
deliberately volatile). Teardown: `sudo scripts/quickstart.sh --down`.

What it does, in order: preflight (AVX, docker) → sample clip (+ a one-time
1080p30 conform if ffmpeg is present) → a self-contained lower-third page on
`:8085` → six containers with the exact wiring of the live
[mxlswitcher.com](https://mxlswitcher.com) deployment → starts the writers →
**discovers their flow UUIDs at runtime** with `mxl-info` → selector → keyer →
encoder. The final banner prints your viewer URL and the control one-liners.

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

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `illegal instruction` in any container log | No AVX. QEMU: CPU model `host`. (§11) |
| Viewer page loads, video black | `8189/udp` closed, or `MTX_WEBRTCADDITIONALHOSTS` didn't get your public IP (re-run the script; it re-detects). |
| Selector start returns 400 `flow_def.json not found` | A writer died before the selector attached — `docker logs test-generator file-player`, then re-run. |
| Cut works but shows a frozen/old frame | Reader wedged on a recreated flow — the fundamental MXL gotcha. Restart the selector pipeline (stop + start with the same body). §6. |
| Clip stutters on cuts | Non-30fps source vs the 30/1 domain. Install ffmpeg and re-run (it conforms the clip), or bring a 1080p30 file. §8. |

## Where to go next

Each step is one tool from [`tools/`](../tools/), and
[`scripts/bring-up-mxl.sh`](../scripts/bring-up-mxl.sh) shows all of them
assembled (it's the live demo's actual cold-start):

1. **Your camera as a cuttable input** — point any SRT/RTSP source at the box
   and adapt [`tools/cam_ingest.py`](../tools/cam_ingest.py) (the
   cadence-preserving re-stamp inside it is *the* trick that makes a remote
   source cut cleanly; FINDINGS §1).
2. **Preview thumbnails** — [`tools/mxl_thumbs.py`](../tools/mxl_thumbs.py).
3. **Program audio / audio-follow-video** — [`tools/audio_pgm.py`](../tools/audio_pgm.py).
4. **Open guest contribution** — [`tools/guest_ingest.py`](../tools/guest_ingest.py)
   (+ `guest_audio.py`), phone-scan-to-air included.
5. **Layouts as an input** — [`tools/layout_pgm.py`](../tools/layout_pgm.py).
6. **A multiview wall, hardware-style** — [`tools/mxl_multiview.py`](../tools/mxl_multiview.py)
   + [`tools/mv_encode.py`](../tools/mv_encode.py) (FINDINGS §10 before you
   "clean up" either).
7. **Record into TAMS / cross-host fabric** — [TAMS.md](TAMS.md),
   [JONAS-FABRIC-HANDOFF.md](JONAS-FABRIC-HANDOFF.md).

Read [FINDINGS.md](FINDINGS.md) *before* you debug anything. Every numbered
section is an outage we already had so you don't have to.

**Want the concepts, not just the facility?** The upstream
[cbcrc/mxl-hands-on Exercises](https://github.com/cbcrc/mxl-hands-on/tree/main/Exercises)
(single writer → multiple domains → GUI probing → "full open-source DMF") are
the guided-learning track; this quickstart is the everything-running-now
track. They compose well: do Exercise 1 once and the flow list above stops
being magic.
