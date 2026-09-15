# Building the MXL Cloud Production Demo on GCP

> **✅ VERIFIED 2026‑09‑15.** The `scripts/quickstart.sh` path ran clean end‑to‑end
> on a fresh GCP **n2‑standard‑8** (us‑west1‑a, Ubuntu 24.04, AVX‑512): MXL
> switcher ON AIR, keyer lower‑third rendering, live CUT/pattern control, full
> flow set in `/dev/shm/mxl/domain_1`, WebRTC program served to the browser.
> Proof: `docs/images/screenshots/gcp-on-air.png`. **The same one command that
> works on Azure works on GCP — portability proven.** The full 32‑core two‑host
> rig awaits a `CPUS_ALL_REGIONS` quota bump (default 12; see §3).

> **Two‑host fabric, 2026‑09‑15 (partial):** Quota bump approved (12→48 in ~1 min),
> full 32‑core Host 1 + 8‑core Host 2 both up in one VPC/zone, and
> `mxl-fabrics-demo` (libfabric 2.6.0 + MXL v1.1.0) built from source on both. The
> **fabric link is proven**: initiator on Host 1 connects to the target on Host 2
> across the VPC (`10.10.0.10 → 10.10.0.11:1313`, tcp provider, no NAT/sockaddr
> patch needed same‑subnet), the target accepts, and the flow **materializes** in
> Host 2's `domain_fabric` (`data`, `flow_def.json`, `grains/`). **Grain *transfer*
> was not confirmed** — the initiator connects, logs "batch size 1080 slices", then
> emits no rate; the demo tool exposes no transfer diagnostics and `mxl-info`'s
> "grain count" is the ring size, not an advancing head, so advancement couldn't be
> measured from outside. Consistent with the FINDINGS note that the fabric layer is
> finicky and under‑instrumented. **Build recipe gotchas found (missing from
> JONAS‑FABRIC‑HANDOFF):** must `git clone + bootstrap vcpkg` at `~/vcpkg` first
> (the preset hardcodes that path); there are **no build presets**, so build with
> `cmake --build build/Linux-GCC-Release -j$(nproc)`, not `--build --preset`.

**Status: single‑host proof DONE; two‑host fabric link proven, grain‑flow unconfirmed.** This plan maps the running Azure facility onto
Google Compute Engine, sized to a **$300 free-credit window (expires 2026‑12‑02)**.
The point is a *portability proof*: the same stack — Linux + shared memory —
running identically on a third cloud, reinforcing the "no vendor lock-in, general
compute" thesis for the standards community. Companion to
[`AWS-BUILD-PLAN.md`](AWS-BUILD-PLAN.md) and the Azure rig in the
[README](../README.md).

## 0. Why bother (and the honest framing)

The MXL/TAMS switcher has nothing Azure-specific in it: an AVX-capable Xeon, a
big `/dev/shm`, libfabric over TCP, cloudflared, ffmpeg. If it runs on Azure and
AWS, it runs on GCP. Standing it up on GCP once — screenshot/record it, tear it
down — is a cheap, strong data point: *"the open standard doesn't care whose
cloud you're on."* Not a permanent home; a proof.

## 1. Azure → GCP translation table

| Role | Azure (as run) | GCP equivalent | Why |
|---|---|---|---|
| **Host 1 — production** (domain, switcher, layout, keyer, encoder, mediamtx) | `D32s_v5` (32 vCPU / 128 GB, Ice Lake Xeon) | **`n2-standard-32`** (32 vCPU / 128 GB, Cascade/Ice Lake Xeon) | 1:1 on cores **and** RAM. **N2 has AVX‑512** — required (MXL v210 conversion leans on AVX; our on-prem no-AVX box can't run it). N2 (not C3) because C3 has no ‑32 SKU and defaults to 4 GB/vCPU; the domain lives in `/dev/shm`, so 128 GB matters. |
| Host 2 — contribution (guest SRT ingest, fabric initiators, TAMS shipper) | `D8s_v5` (8 / 32) | **`n2-standard-8`** (8 / 32) | 1:1. Also AVX‑512. |
| ~~Host 3~~ | `D4s_v5` (TAMS store + island tests) | **not needed** | Native **GCS** replaces the self-hosted object store; the lightweight TAMS API rides on Host 2. (Same simplification AWS gets with S3.) |
| Network | single VNet 10.0.0.x + NSG | **one VPC + firewall rules**, or two subnets (prod + contribution DMZ) for guest isolation | Keep both hosts **in one zone** — see §2. A DMZ subnet for stranger guests mirrors the AWS plan's stricter posture. |
| Public URLs | Cloudflare tunnels (`cloudflared`) | **Same.** cloudflared runs identically on GCE | Don't replace what works. WebRTC media goes UDP-direct to the instance's external IP, never through the tunnel. |
| TAMS object store | MinIO-style store on VM3 (:9000) | **Native GCS + V4 signed URLs** | TAMS is happy on any S3-compatible store; GCS's S3 interop or the native API both work. Drop the self-hosted store. |
| Auth to cloud | Service Principal | **Service account attached to the instances** | No long-lived keys on the boxes; scope it to the one GCS bucket. |

## 2. The cost warnings that actually matter (on a $300 budget)

$300 is generous for a *proof* and tight for *always-on*. The math:

- **Compute is the whole budget.** `n2-standard-32` ≈ **$1.55/hr**, `n2-standard-8`
  ≈ **$0.39/hr** → the pair is **≈ $1.94/hr**. That's **~150 hours (~6 days)** of
  24/7 runtime on $300 — or, deallocating when idle (which we already do
  religiously), **weeks** of real working sessions. A 2-day build + first show
  is **≈ $55–60**, leaving most of the credit intact.
- **Never send a raw v210 fabric flow across a zone or the internet.** One
  uncompressed 1080p30 flow is **~1.3 Gbps ≈ 585 GB/hr**. GCP inter-zone egress
  is ~$0.01/GB → **~$5.85/hr per flow**, and internet egress is far worse. **Put
  Host 1 and Host 2 in the same zone** (e.g. `us-west1-a`); intra-zone traffic
  between VMs on internal IPs is free. Same-zone is non-negotiable for the fabric.
- **Only the encoded program leaves the cloud** (~5 Mbps WebRTC, UDP-direct). A
  booth/viewer crowd is beer money, not rent — same as Azure/AWS.
- **Stop instances when idle.** A *stopped* GCE instance drops compute to $0;
  you keep only the persistent disk (pd-balanced ≈ $0.10/GB‑month). Same
  discipline as `az vm deallocate`.
- **Credits expire 2026‑12‑02** — this is a time-boxed proof, not infrastructure
  to lean on. Do the run, capture it, tear down.

## 3. The one real blocker: the GLOBAL CPU quota (`CPUS_ALL_REGIONS`)

**Confirmed in practice 2026‑09‑15:** the gate is *not* the regional quota (us‑west1
`CPUS`/`N2_CPUS` were already 100). It's the **global `CPUS_ALL_REGIONS` cap, which
defaulted to 12** on this just‑upgraded account. Host 2 (n2‑standard‑8) consumed 8
of it; Host 1 (n2‑standard‑32) needs 32 → **blocked** with:
`Quota 'CPUS_ALL_REGIONS' exceeded. Limit: 12.0 globally.` A 32 + 8 = **40 vCPU**
rig needs this raised.

- **Free-trial accounts can't request increases and are capped at 8 cores.** This
  is an **upgraded/full account**, so increase requests are available.
- **Action:** Console → IAM & Admin → Quotas → filter **"CPUs (all regions)"**
  (`CPUS_ALL_REGIONS`, *not* the regional one) → Edit Quotas → request **48**
  (headroom over 40). Upgraded accounts usually auto‑approve in minutes–hours.
  The CLI in SDK 584 has no quota‑update verb; use the Console.
- **The fallback proof is already done** (see the banner at the top): the full
  keyed switcher runs under 8 vCPU, so portability is proven *now*; the 32‑core
  rig only adds headroom for two real cameras + multiview + guests.

## 4. Per-host software builds (identical to Azure/AWS)

Nothing changes below the instance layer — that's the whole point.

- **Both hosts:** Ubuntu 24.04, build the MXL SDK + `mxl-fabrics-demo`, libfabric
  ≥ 2.x (built 2.6.0 on Azure), ffmpeg, cloudflared. Verify `grep avx /proc/cpuinfo`
  is non-empty first (N2 → yes).
- **Host 1:** the domain in `/dev/shm/mxl`, selector/keyer/layout/audio writers,
  the multiview compositor, mediamtx (SRT in / WebRTC out), the switcher backend.
- **Host 2:** guest SRT ingest, fabric initiators to Host 1, the TAMS shipper
  (pointing at GCS instead of MinIO).
- **TAMS:** gateway + CouchDB on Host 2; object store = GCS bucket via signed
  URLs. (Carry over the 30-min edge-cache workaround for the flat CouchDB
  timerange query — see `tams-gateway-couchdb-perf.md`.)

## 5. Build phases

1. **Pre-flight:** create project (already have `vertical-task-507419-v6`), enable
   Compute Engine + Cloud Storage APIs, **file the 48-vCPU quota bump**, pick one
   zone.
2. **Host 1 up** (`n2-standard-32`, one zone), build SDK/fabric/ffmpeg, bring up
   the domain + switcher, cloudflared for the public program.
3. **Host 2 up** (`n2-standard-8`, same zone), contribution + fabric to Host 1 +
   TAMS→GCS shipper.
4. **Prove it:** cut the show, clip from TAMS, export a Short — capture
   screenshots/recording for the archive as "also runs on GCP."
5. **Tear down:** stop both instances (or delete + keep the boot images), confirm
   $0 compute.

## 6. What it costs (proof-sized)

| Line | Rate | A realistic 2-day build + first show |
|---|---|---|
| `n2-standard-32` × ~25 hrs | $1.55/hr | ~$39 |
| `n2-standard-8` × ~25 hrs | $0.39/hr | ~$10 |
| Viewer egress, GCS, disks | — | ~$6 |
| **Total** | | **≈ $55 of the $300 credit → a working GCP facility** |

Leaves ~$245 for re-runs, a longer soak test, or a second demo before the credit
expires. The single largest way to blow the budget is a cross-zone fabric flow —
don't; keep both hosts in one zone (§2).

## 7. Open decisions (for Guy)

- **Region/zone:** default suggestion `us-west1` (Oregon) — near the US studio
  contribution path, and typically lower-cost than us-central for N2. Confirm.
- **Full 32-core proof vs. reduced 8-core proof** while quota is pending.
- **Is this worth building at all before the credits expire, or capture the plan
  and revisit?** This doc is the plan either way.

## 8. Field notes from the GCP multi-cam attempt (2026-09-15)

Real gotchas hit standing up Cam1 + Cam2 + Playout on GCP (all reproducible, all
worth knowing before you try):

- **Selector requires a uniform grain rate.** A PTZ pushing native **1080p60**
  lands as a 60/1 flow; the selector rejects the input set with
  `Input formats do not match — grain_rate 60/1 ≠ 30/1`. Fix: normalize on ingest
  with `videorate ! video/x-raw,framerate=30/1` (or force `-r 30` on the SRT push).
- **Never reconfigure a *running* selector.** `stop`→`start` on a live selector to
  add inputs cascades: the keyer/encoder lose their source and the WebRTC WHIP
  publish wedges ("stream not found"). Build the full input set at first start,
  in dependency order (writers → selector → keyer → encoder), the way
  `scripts/bring-up-mxl.sh` does — do NOT hot-add inputs.
- **The encoder's `video_flow_uuid` is the KEYER OUTPUT, which is deterministic
  from the keyer's label/description** (byte-exact), not a random runtime UUID.
  In `bring-up-mxl.sh` that's `5c73394e-…` (keyer-pgm-flow.json). Point the
  encoder at the *derived* UUID, and discover UUIDs by label at each step rather
  than assuming.
- **Real PTZ → GCP over SRT works.** Pushing `rtsp://192.168.6.89` from a studio
  box to `srt://<host-ip>:8890?streamid=publish:cam1` landed the live camera on
  GCP end to end. The PTZ's **built-in SRT sender** is the optimized path (no
  ffmpeg transcode hop). Only one publisher per SRT stream name — kill any
  synthetic test pusher on the same `publish:cam1` first.
- **cam_ingest zombies + SSH `pkill` hazard.** `pkill -f run-camN.sh` self-matches
  the killing shell; on an already-running box it leaves duplicate ingest
  supervisors fighting over one flow-id. Use file-based runners whose cmdline
  carries a unique token, and verify exactly one reader per flow.

## 9. Real cameras on GCP — proven, + the one open bug (2026-09-15)

**Achieved (all verified):**
- **Real studio PTZ (PTZOptics F63, 192.168.6.89) live on GCP** — RTSP→SRT relay
  (`mxl-cam-push`, `-c:v copy`, latency=200) → GCP mediamtx → cam_ingest → MXL
  `/dev/shm`. Pulled a full clean 1080p frame from the domain flow
  (`gcp-real-ptz-on-gcp.jpg`).
- **Makito X4 Cam 2 (192.168.8.177)** repointed Azure→GCP via its REST API
  (`PUT /apis/streams/2`, address→34.83.212.72, latency 200) — held **13+ min
  stable, 12.7 Mbps, 1 reconnection**. (Stream 1 "Extra Hours SRT"=YouTube, untouched.)
- **SRT latency for WAN:** the Azure 20ms (tuned for ~6ms studio↔VM LAN) was too
  tight for the studio→GCP-Oregon internet hop; **200ms** made both feeds hold.
- **Selector cuts 4 real inputs** — verified its OUTPUT flow carries the live PTZ
  frame (grabbed directly). Clean domain = 8 flows.

**The one open bug — `mxl2webrtc` WHIP egress on the minimal quickstart:**
Reproduced ~10× across reboots + clean domains + correct ordering. Upstream is
perfect (cameras land, selector/keyer/encoder run, domain clean), and the encoder
logs `WHIP handshake complete — streaming to MediaMTX`, but the browser gets
`stream not found` — mediamtx never serves the `mxl2webrtc` path. Suspect the
containerized WHIP client ↔ this mediamtx (host.docker.internal resolution / WHIP
path / version). **The Azure rig avoids this** because it runs the full backend +
`/api/mxl/repair` orchestration (encoder re-attach), which the quickstart lacks.
**Fix path:** replicate the full `scripts/bring-up-mxl.sh` + backend on cloud
(the AWS recorded build), don't hot-reconfigure the minimal quickstart.

**Gotcha that caused hours of freezes:** repeatedly reconfiguring the live
selector/keyer/encoder littered `/dev/shm` with **45 stale `.mxl-flow` dirs**;
readers/encoder then read stale duplicates → frozen program. A reboot (wipes
tmpfs) + ONE ordered bring-up = clean 8-flow domain. **Never hot-reconfigure;
bring up once, in order.**
