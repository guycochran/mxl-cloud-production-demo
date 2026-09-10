# Receive our live program as raw MXL grains — fabric handoff

**The offer:** your easy-mxl is already orchestrating our whole production
(domain `domain_1` on our Azure box). This recipe sends that production's
**Keyer PGM back to your machine as uncompressed v210 grains over the MXL
fabric (v1.1.0, TCP provider)** — no codec anywhere between our compositor
and your `/dev/shm`. We run the initiator; you run a target. Field-proven
across isolated Azure VNets at a sustained 30 grains/s.

## Bandwidth reality check (pick your flow)

| Flow | Rate | Fits |
|---|---|---|
| `5c73394e-…` Keyer PGM, 1920×1080p30 v210 | **~1.32 Gbps** | >1GbE only (cloud VM, 2.5/10GbE NIC) |
| `119070e0-…` PGM Lite, 960×540p30 v210 | **~0.33 Gbps** | any GigE laptop / decent venue wired |

A venue-WiFi laptop cannot take either. Wired GigE → ask us for PGM Lite
(we flip it on in ~10s). Your own cloud VM → full PGM, and you can hang
your mxl2webrtc on it for a public "our program, received on CLOUDflex
infra" viewer.

## Your side (Ubuntu 22.04/24.04, ~20 min with cache luck)

1. **libfabric ≥ 2.x** (distro 1.x lacks `FI_SOCKADDR_IP`):
   ```bash
   wget https://github.com/ofiwg/libfabric/releases/download/v2.6.0/libfabric-2.6.0.tar.bz2
   tar xf libfabric-2.6.0.tar.bz2 && cd libfabric-2.6.0
   ./configure --enable-tcp --enable-shm && make -j$(nproc) && sudo make install && sudo ldconfig
   ```
2. **mxl v1.1.0 with fabrics** (gotchas we hit: shallow vcpkg clones fail
   baseline resolution — `git fetch --unshallow`; extra apt deps
   `ninja-build bison flex libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev`):
   ```bash
   git clone https://github.com/dmf-mxl/mxl && cd mxl && git checkout v1.1.0
   cmake --preset Linux-GCC-Release -DMXL_ENABLE_FABRICS_OFI=ON
   cmake --build --preset Linux-GCC-Release
   ```
3. **Domain + flow def** (grab `keyer-pgm-flow.json` from us — or from this
   repo's `docs/` — it must match byte-for-byte so the UUID derives right):
   ```bash
   mkdir -p /dev/shm/mxl/domain_fabric
   ```
4. **Run the target** (pick a TCP port you can reach from the internet —
   port-forward it if you're behind NAT):
   ```bash
   ./build/Linux-GCC-Release/tools/mxl-fabrics-demo/mxl-fabrics-demo \
     -d /dev/shm/mxl/domain_fabric -p tcp -n <YOUR-LAN-IP> -s <PORT> \
     -f keyer-pgm-flow.json -t @target.json
   ```
5. **Send us `target.json` + your public IP + port.** That file carries your
   rkeys and your *local* sockaddr; if you're NATed we patch the embedded
   address to your public IP (bytes[4:8] of the base64 `fabricAddress` —
   see dmf-mxl#714; our patch script is in this repo as
   `tools/patch-target-ip.py`). Note: **every target restart regenerates
   rkeys** — resend target.json after any restart.

## Our side (10 seconds once we have your file)

```bash
~/fabric/jonas/start-jonas-leg.sh target.json <your-public-ip> [flow-uuid]
```

## What you should see

Within ~10s your target prints `30.0 grains/s | avg 1080.0 slices/grains`
(540-slice for Lite) and `/dev/shm/mxl/domain_fabric/<uuid>.mxl-flow` head
tracks our program in real time. Point your own mxl2webrtc (or easy-mxl's
catalog viewer) at that domain and you're watching our switcher — cuts,
2-up/PiP layouts, keyed graphics, phone contributors — delivered to your
machine as shared-memory grains.

Known sharp edges from our week of field notes (`docs/FINDINGS.md`):
readers wedge if a flow is recreated after attach (restart the reader);
targets rewrite rkeys per start; grain timestamps are ring addresses.
