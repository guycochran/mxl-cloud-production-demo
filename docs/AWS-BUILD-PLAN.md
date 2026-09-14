# Building the MXL Cloud Production Demo on AWS

**Goal:** run the facility we proved on Azure for IBC 2026 — uncompressed
v210 shared-memory switching, multi-host MXL fabric, guest SRT
contribution, TAMS recording/clipping, public WebRTC program — **as a
production deployment on AWS**. Everything in this repo (tools/,
bring-up, FINDINGS) runs unchanged; only the infrastructure vocabulary
translates. (The Azure run already proved cloud-neutrality, including a
cross-network fabric island — production doesn't need to re-prove it.)

Audience: anyone with an AWS account and a weekend. We spent ~6 days on
Azure *discovering* everything in `FINDINGS.md`; a replay with these
recipes is a 1–2 day job.

---

## 1. Azure → AWS translation table

| Role | Azure (as run) | AWS equivalent | Why |
|---|---|---|---|
| **Host 1 — production** (domain, switcher, layout, keyer, encoder, mediamtx) | `D32s_v5` (32 vCPU / 128 GB, Xeon 8370C) | **`m6i.8xlarge`** (32 vCPU / 128 GB, Xeon 8375C) | Near-identical silicon. The MXL domain lives in `/dev/shm` — RAM matters as much as cores, so m6i over c6i. |
| Host 2 — contribution (guest SRT ingest, fabric initiators, TAMS shipper) | `D8s_v5` (8 / 32) | **`m6i.2xlarge`** (8 / 32) | 1:1. |
| ~~Host 3~~ | `D4s_v5` (TAMS store + island tests) | **not needed** | Native S3 replaces the self-hosted object store; the lightweight TAMS API rides on Host 2. Production has no island. |
| VNet + NSG | single VNet 10.0.0.x, NSG rules | **TWO VPCs + peering** — prod and a contribution DMZ | Stricter than our Azure rig, deliberately: guests are strangers; isolate their landing zone from the program chain. §3. |
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
   - Same-AZ private-IP traffic — **including over VPC peering** — is
     **free**. → **Put Host 1 and Host 2 in the same AZ.** Non-negotiable.
   - Cross-AZ: $0.01/GB each way ≈ **$11.70/hour per flow**. Never run the
     fabric cross-AZ by accident.
   - Internet egress ($0.09/GB): never point a raw fabric flow at a public
     IP in production — that's ≈$52/hour per 1080p flow. Fabric stays on
     private IPs, period; only the encoded program (~5 Mbps) leaves.
2. **Viewer egress is fine.** Each WebRTC viewer is ~5 Mbps H.264 ≈
   2.3 GB/hr ≈ $0.20/hr. A booth crowd costs beer money, not rent.
3. **Stop instances when idle.** EC2 *stop* keeps the EBS volumes
   (~$0.08/GB-month for gp3) and drops compute to zero — same discipline as
   our Azure deallocate rule.
4. **Ballpark production rate** (2 hosts, us-west-2, on-demand):
   `m6i.8xlarge` $1.536 + `m6i.2xlarge` $0.384 ≈ **$1.92/hr + viewer
   egress**. Show-hours-only with stop-when-idle: a few dollars per
   production day. If it becomes an always-on facility, a 1-year Compute
   Savings Plan takes ~40% off (≈$850/mo for 24/7); use Spot only for
   rehearsals, never on air.
5. **New-account quota:** fresh AWS accounts often cap Running On-Demand
   Standard vCPUs at 32 or fewer. **File the quota increase to 48 vCPU on
   day 0** (32+8 running, headroom for a resize) — it can take a day to approve and it gates the whole build.

---

## 3. Network design — two VPCs (production + contribution DMZ)

Guests are strangers with a QR code. Their landing zone gets its own VPC:
a compromised or misbehaving contribution host has **no route** to the
program chain except the two fabric ports we explicitly peer. This is the
broadcast ingest-DMZ pattern, in cloud vocabulary.

```
                                    THE INTERNET
        ┌──────────────┐   ┌───────────────┐   ┌─────────────────────────┐
        │ Guests        │   │ Studio cams   │   │ Viewers + Operators     │
        │ Larix / vMix  │   │ PTZ, Makito   │   │ switcher UI + WebRTC    │
        └──────┬────────┘   └──────┬────────┘   └───────────▲─────────────┘
               │ SRT (UDP 8890)    │ SRT (UDP 8890)         │ WebRTC (UDP 8189)
               ▼                   ▼                        │ + HTTPS via cloudflared
╔══════════════════════════╗  ╔═════════════════════════════╧════════════════╗
║ VPC-CONTRIB  10.1.0.0/16 ║  ║ VPC-PROD  10.0.0.0/16                        ║
║ ┌──────────────────────┐ ║  ║ ┌──────────────────────────────────────────┐ ║
║ │ Host 2  m6i.2xlarge  │ ║  ║ │ Host 1  m6i.8xlarge   (32c/128G, AVX)    │ ║
║ │  mediamtx (SRT in)   │ ║  ║ │  /dev/shm MXL domain (uncompressed v210) │ ║
║ │  guest_ingest ×2 ────┼─╫──╫─▶  fabric targets :1314-1315               │ ║
║ │  guest_audio ×2      │ ║  ║ │  cam/cam2 ingest ─ relay ─┐              │ ║
║ │  fabric initiators   │ ║  ║ │  layout_pgm (2up/PiP/4up) ├─ SELECTOR    │ ║
║ │  fabric PGM target ◀─┼─╫──╫─┤  audio_pgm, test gen ─────┘    │         │ ║
║ │       │              │ ║  ║ │                    HTML5 KEYER ─┴─ ENCODER│ ║
║ │  tams_shipper        │ ║  ║ │                        │            │     │ ║
║ │  TAMS API :8000      │ ║  ║ │  grain_probe, thumbs, multiview  mediamtx │ ║
║ └───────┬──────────────┘ ║  ║ └──────────────────────────────────────────┘ ║
╚═════════╪════════════════╝  ╚══════════════════════════════════════════════╝
          │       ▲______________________▲
          │        VPC PEERING pcx-…  (BOTH HOSTS IN THE SAME AZ = $0/GB)
          │        route: 10.0.0.0/16 ⇄ 10.1.0.0/16, fabric TCP only in SGs
          │        guests → :1314-1315 (prod)   keyed PGM ← :1313 (contrib)
          ▼
   Amazon S3 (regional) — s3://…-mxl-tams/ : 1s segments + sprites,
   presigned URLs straight to the clipper UI. Reached from BOTH VPCs via
   S3 Gateway Endpoints ($0/GB, traffic never touches the internet).
```

Provisioning specifics:

- **Peering:** one `pcx` between the VPCs, routes for each other's CIDR in
  both route tables. Same region, same AZ (pick the AZ by *name mapping*
  in both VPCs — AZ letters shuffle per account; use AZ IDs like
  `usw2-az1` to be sure both subnets truly share an AZ).
- **Security groups** (cross-VPC SG references don't work over peering —
  use CIDR rules):
  - `sg-prod` (Host 1): UDP 8890 world (studio cams), UDP 8189 world
    (WebRTC), TCP 1314-1315 from 10.1.0.0/16 (guest fabric in), TCP 9600-
    9700 + 8554 from 10.1.0.0/16 + admin IP, SSH from admin IP.
  - `sg-contrib` (Host 2): UDP 8890 world (guest SRT — this is the DMZ's
    whole job), TCP 1313 from 10.0.0.0/16 (PGM fabric back for TAMS),
    TCP 8000 from admin IP or via cloudflared (TAMS API), SSH admin IP.
  - cloudflared needs **no inbound anywhere**.
- **S3 Gateway Endpoints** in both VPCs (route-table entries, free) + IAM
  instance roles scoped to the TAMS bucket.
- **Jumbo frames:** MTU 9001 works *within* each VPC but **peering clamps
  to 1500** — set the fabric sockets/NICs accordingly or leave MTU 1500 on
  the fabric path (our Azure rig ran 1500 at 1.3 Gbps without complaint,
  so this costs nothing we ever had).
- **No cluster placement group** (can't span VPCs). Same-AZ RTT ≈ 0.5 ms —
  an order of magnitude better than anything our fabric needed.
- **Elastic IPs** on both hosts (stable targets for SRT publishers and
  WebRTC; free while attached to running instances).

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

**Phase 0 — account prep (day 0, async):** vCPU quota request (§2.5),
BOTH VPCs + same-AZ-ID subnets + peering + routes + SGs (§3), 2× EIP,
S3 bucket + gateway endpoints in both VPCs, IAM instance roles with
scoped S3 access.

**Phase 1 — single-host production (half day):** launch `m6i.8xlarge`, run
the §4 build, adapt `bring-up-mxl.sh` (it's already the executable
documentation of the whole VM1 stack: containers, ingests, layout, thumbs,
probe, keyer, encoder). Success = test-generator program visible over
WebRTC + cuts working from the web UI.

**Phase 2 — contribution DMZ + fabric (half day):** launch `m6i.2xlarge`
in VPC-CONTRIB (same AZ ID!). Guest SRT → ingest → **MXL fabric TCP over
the peering, private IPs** → host 1 domain. First test after bring-up:
`iperf3` across the peering to confirm same-AZ placement (≥4 Gbps and
sub-ms RTT — if you see ~1 Gbps and 1-2 ms you've crossed AZs and §2.1
applies; fix before anything else). Success = phone on Larix appears on a Guest button and cuts to
program uncompressed. Expect the *same* numbers we logged: 30 grains/s,
avg 1080 slices/grain, ~1.3 Gbps/flow. Port the guest-leg doctor with the
legs — the fabric wedge species (FINDINGS) travel with the software, not
the cloud.

**Phase 3 — TAMS on real S3 (half day):** TAMS API server on host 2,
segments to **native S3 presigned URLs**. The clipper
(`web/mxl-tams.html`) works unchanged — it already speaks
presigned-object-URL. No self-hosted object store, no third host.
(If a remote/cross-region receiver is ever wanted later, the recipe is
`tools/patch-target-ip.py` + FINDINGS — proven on Azure, not part of
this production build.)

**Phase 4 — production hardening (half day):** grain probe + health
board, guest-leg doctor, kiosk idle-reset, the multiview wall, and
`flow_stabilizer.py` deployed **from day 1** (see §7.2). All ports of
existing tools.

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

## 8. What it costs

| Line | Rate | A realistic 2-day build + first show |
|---|---|---|
| m6i.8xlarge × ~25 hrs | $1.536/hr | ~$38 |
| m6i.2xlarge × ~25 hrs | $0.384/hr | ~$10 |
| Viewer egress, storage, EIPs, S3 | — | ~$8 |
| **Total** | | **≈ $56 to a working production facility** |

Ongoing: ~$2/hr during show hours, near-zero stopped. Always-on with a
Savings Plan ≈ $850/mo (§2.4).

---

*Everything referenced here lives in this repo: `bring-up-mxl.sh` (host 1
runbook-as-script), `tools/` (all pipeline programs), `docs/FINDINGS.md`
(the failure modes you will meet and their fixes),
`docs/JONAS-FABRIC-HANDOFF.md` (fabric build recipe), `docs/TAMS.md`.
Same software, different cloud — that's the point.*
