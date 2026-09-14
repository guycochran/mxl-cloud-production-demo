# Building the MXL Cloud Production Demo on AWS

**Goal:** stand up the *exact* production we ran on Azure for IBC 2026 —
uncompressed v210 shared-memory switching, multi-host MXL fabric, guest
SRT contribution, TAMS recording/clipping, public WebRTC program — on AWS,
to prove the stack is genuinely cloud-neutral. Everything in this repo
(tools/, bring-up, FINDINGS) runs unchanged; only the infrastructure
vocabulary translates.

Audience: anyone with an AWS account and a weekend. We spent ~6 days on
Azure *discovering* everything in `FINDINGS.md`; a replay with these
recipes is a 1–2 day job.

---

## 1. Azure → AWS translation table

| Role | Azure (as run) | AWS equivalent | Why |
|---|---|---|---|
| **Host 1 — production** (domain, switcher, layout, keyer, encoder, mediamtx) | `D32s_v5` (32 vCPU / 128 GB, Xeon 8370C) | **`m6i.8xlarge`** (32 vCPU / 128 GB, Xeon 8375C) | Near-identical silicon. The MXL domain lives in `/dev/shm` — RAM matters as much as cores, so m6i over c6i. |
| Host 2 — contribution (guest SRT ingest, fabric initiators, TAMS shipper) | `D8s_v5` (8 / 32) | **`m6i.2xlarge`** (8 / 32) | 1:1. |
| Host 3 — TAMS store / remote-island receiver | `D4s_v5` (4 / 16) | **`m6i.xlarge`** (4 / 16) | 1:1. |
| VNet + NSG | single VNet 10.0.0.x, NSG rules | **VPC + Security Groups** | See §3 for the exact port set. |
| Isolated-island test (VM3 in separate VNet/region) | isolated VNet, public-IP fabric, sockaddr patch | **separate VPC** (or separate *region* for a bigger wow) + `tools/patch-target-ip.py` | Identical mechanics — the TargetInfo sockaddr patch (FINDINGS, dmf-mxl#714) is cloud-agnostic. |
| Public URLs | Cloudflare tunnels (`cloudflared`) | **Same.** cloudflared runs identically on EC2 | Don't replace what works. WebRTC media never rode the tunnel anyway — it goes UDP-direct to the instance (Elastic IP). |
| TAMS object store | MinIO-style store on VM3 (:9000) | **Native S3 + presigned URLs** | TAMS was *designed* for S3. This is the one place AWS is an upgrade, not a translation — drop the self-hosted object store entirely. |
| Auth to cloud | Service Principal | **IAM role on the instances** | Instance profiles; no long-lived keys on boxes. |

**CPU requirement:** anything current-gen Intel/AMD is fine, but v210
conversion leans on **AVX** — that's why our on-prem box (no AVX) can't run
MXL at all. Every m6i/c6i/m7i has it. Don't use burstable `t3/t4g` for any
media host (t-class CPU credits will strand a 30fps pipeline mid-demo).

---

## 2. The cost warnings that actually matter

These are the traps for a personal-account build. Read before launching.

1. **Cross-AZ data transfer will eat you alive.** An MXL fabric flow is
   **~1.3 Gbps of uncompressed v210 — ≈585 GB/hour, continuously.** On
   Azure (grant) we never felt it. On AWS:
   - Same-AZ private-IP traffic: **free**. → **Put Host 1 and Host 2 in the
     same AZ, in a cluster placement group.** Non-negotiable.
   - Cross-AZ: $0.01/GB each way ≈ **$11.70/hour per flow**. Never run the
     fabric cross-AZ by accident.
   - Internet egress ($0.09/GB): the public-IP island test (§6 Phase 4)
     costs ≈ **$52/hour per 1080p flow**. Run it for the screenshot and the
     numbers, then shut it down. (Cross-*region* over VPC peering is
     $0.02/GB ≈ $11.70/hr — cheaper way to show geography.)
2. **Viewer egress is fine.** Each WebRTC viewer is ~5 Mbps H.264 ≈
   2.3 GB/hr ≈ $0.20/hr. A booth crowd costs beer money, not rent.
3. **Stop instances when idle.** EC2 *stop* keeps the EBS volumes
   (~$0.08/GB-month for gp3) and drops compute to zero — same discipline as
   our Azure deallocate rule.
4. **Ballpark demo-day rate** (3 hosts, us-west-2, on-demand):
   `m6i.8xlarge` $1.536 + `m6i.2xlarge` $0.384 + `m6i.xlarge` $0.192 ≈
   **$2.11/hr + viewer egress**. Left running 24/7 that's ~$1,550/mo —
   don't. (Spot is ~60-70% off and fine for rehearsal days; don't demo to
   Jonas on spot.)
5. **New-account quota:** fresh AWS accounts often cap Running On-Demand
   Standard vCPUs at 32 or fewer. **File the quota increase to 64 vCPU on
   day 0** — it can take a day to approve and it gates the whole build.

---

## 3. Network design

```
VPC 10.0.0.0/16, one subnet per AZ, hosts 1+2 in the SAME subnet/AZ
└── cluster placement group "mxl-fabric"  (sub-100µs RTT host1↔host2)

Security groups (principle: media in, management locked):
  sg-media (hosts 1,2):
    UDP 8890            SRT contribution (guests + cameras) — world or geo-scoped
    UDP 8189            WebRTC media — world
    TCP 1312-1315       MXL fabric legs — sg-internal only (private IPs!)
    TCP 8554            RTSP — sg-internal only
    TCP 8888-8889       HLS/WHEP — via cloudflared, so internal only
    TCP 9600-9700       easy-mxl / pipeline APIs — sg-internal + admin IP
  sg-admin: TCP 22 from your IP only. cloudflared needs NO inbound at all.
```

Two AWS-specific wins over our Azure setup:

- **Jumbo frames.** VPC MTU 9001 (Azure default was 1500). For a 1.3 Gbps
  TCP fabric flow this is free headroom — set it on both fabric hosts.
- **Placement group.** Our fabric was happiest when RTT was tiny (the whole
  20ms-SRT saga in FINDINGS is about latency margins). Cluster PG gives you
  the best case by default.

Elastic IPs on hosts 1 and 2 (SRT publishers and WebRTC need stable
addresses; an EIP attached to a running instance is free).

---

## 4. Per-host software builds (identical to Azure)

Ubuntu 24.04 AMI everywhere. The build recipe is exactly
`docs/JONAS-FABRIC-HANDOFF.md` §"Your side" plus `bring-up-mxl.sh`:

- **libfabric ≥ 2.x** built from source (distro 1.x lacks `FI_SOCKADDR_IP`)
- **mxl v1.1.0** with `-DMXL_ENABLE_FABRICS_OFI=ON`
  - gotcha: shallow vcpkg clones fail baseline resolution — `git fetch --unshallow`
  - apt deps: `ninja-build bison flex libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev`
- Docker + the easy-mxl container suite (test generator, input-selector,
  HTML5 keyer, file player, mxl2webrtc encoder, hls2mxl) — Jonas's
  orchestrator runs anywhere Docker does
- mediamtx (SRT/RTSP/WebRTC I/O)
- this repo's `tools/`: cam/guest ingests, `layout_pgm.py` (2-up/PiP/4-up
  compositor, 75–90 ms long-poll takes), `audio_pgm.py`, `mxl_thumbs.py`,
  `grain_probe.py` (the wedge-catcher), guest-leg doctor, multiview
- `cloudflared` for every HTTP surface

Domain stays at `/dev/shm/mxl/domain_1`. Nothing in the data plane knows or
cares which cloud it's on — that's the demo's thesis.

---

## 5. What the control plane looks like

Two options, in order of effort:

1. **Reuse the existing backend** (fastest): the prodbots backend
   (`server-enhanced.js`, /api/mxl/*) points at IPs. Change three host
   constants to the AWS EIPs and the same UI drives the AWS rig. Good for
   an A/B "same switcher, two clouds" party trick.
2. **Full-AWS**: run the backend on a `t3.medium` (control plane is the one
   place burstable is fine) + cloudflared. One evening of work.

---

## 6. Build phases

**Phase 0 — account prep (day 0, async):** vCPU quota request (§2.5), VPC +
subnets + SGs + placement group, 2× EIP, S3 bucket for TAMS, IAM instance
role with scoped S3 access.

**Phase 1 — single-host production (half day):** launch `m6i.8xlarge`, run
the §4 build, adapt `bring-up-mxl.sh` (it's already the executable
documentation of the whole VM1 stack: containers, ingests, layout, thumbs,
probe, keyer, encoder). Success = test-generator program visible over
WebRTC + cuts working from the web UI.

**Phase 2 — two-host fabric (half day):** launch `m6i.2xlarge` in the same
PG. Guest SRT → ingest → **MXL fabric TCP over private IPs** → host 1
domain. Success = phone on Larix appears on a Guest button and cuts to
program uncompressed. Expect the *same* numbers we logged: 30 grains/s,
avg 1080 slices/grain, ~1.3 Gbps/flow. Port the guest-leg doctor with the
legs — the fabric wedge species (FINDINGS) travel with the software, not
the cloud.

**Phase 3 — TAMS on real S3 (half day):** TAMS API server on host 3 (or
host 2), segments to **native S3 presigned URLs**. The clipper
(`web/mxl-tams.html`) works unchanged — it already speaks
presigned-object-URL. This phase is the AWS-native flex: no self-hosted
object store.

**Phase 4 — the island (2 hours, then OFF):** `m6i.xlarge` in a *different
VPC or region*, fabric target via public IP with
`tools/patch-target-ip.py`. This reproduces our three-node
isolated-network proof. Mind §2.1 egress; run it hot only for the demo.

**Phase 5 — hardening (half day):** grain probe + health board, doctor
timers, kiosk idle-reset, the multiview wall. All ports of existing tools.

---

## 7. What we'd do differently this time (lessons pre-paid on Azure)

1. **Start at 32 vCPU.** We resized D8→D16→D32 chasing headroom; the full
   rig (4-up layout + audio + CEF keyer + encoder + multiview) idles around
   load 10–13 on 32 cores. Size for the end state.
2. **Deploy readers/writers in dependency order and don't churn writers.**
   Most of FINDINGS is one lesson wearing different costumes: *MXL readers
   die when a writer recreates its flow*. Every operational recipe here
   (repair cascade, doctor, take-check, `flow_stabilizer.py`) exists
   because of it. The stabilizer architecture (proven, debugged, in
   `tools/flow_stabilizer.py`) is the permanent fix — on a fresh AWS build,
   consider deploying it from day 1 instead of retrofitting under fire.
3. **Same-AZ private IPs for all continuous flows** (here it's also the
   cost rule, §2.1).
4. **Keep `grain_probe.py` running from day 1.** Rate checks lie; only
   unique-frames/sec catches a repeat-wedged reader. It found real faults
   within hours of existing.
5. **mediamtx can wedge silently** (accepts publishes, stops serving
   readers) — `docker restart mediamtx` + republish is the recipe. Put it
   in the runbook before the first guest demo.

---

## 8. Cost of the full proof

| Line | Rate | A realistic 3-day build+demo |
|---|---|---|
| m6i.8xlarge × ~30 hrs | $1.536/hr | ~$46 |
| m6i.2xlarge × ~30 hrs | $0.384/hr | ~$12 |
| m6i.xlarge × ~10 hrs | $0.192/hr | ~$2 |
| Island public-IP fabric × 2 hrs | ~$52/hr egress | ~$104 (the single biggest line — keep it short) |
| Viewer egress, storage, EIPs, S3 | — | ~$10 |
| **Total** | | **≈ $175 for the whole proof** |

Ongoing rehearsals with stopped-when-idle discipline: a few dollars a day.

---

*Everything referenced here lives in this repo: `bring-up-mxl.sh` (host 1
runbook-as-script), `tools/` (all pipeline programs), `docs/FINDINGS.md`
(the failure modes you will meet and their fixes),
`docs/JONAS-FABRIC-HANDOFF.md` (fabric build recipe), `docs/TAMS.md`.
Same software, different cloud — that's the point.*
