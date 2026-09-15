# Building the MXL Cloud Production Demo on GCP

> **✅ VERIFIED 2026‑09‑15.** The `scripts/quickstart.sh` path ran clean end‑to‑end
> on a fresh GCP **n2‑standard‑8** (us‑west1‑a, Ubuntu 24.04, AVX‑512): MXL
> switcher ON AIR, keyer lower‑third rendering, live CUT/pattern control, full
> flow set in `/dev/shm/mxl/domain_1`, WebRTC program served to the browser.
> Proof: `docs/images/screenshots/gcp-on-air.png`. **The same one command that
> works on Azure works on GCP — portability proven.** The full 32‑core two‑host
> rig awaits a `CPUS_ALL_REGIONS` quota bump (default 12; see §3).

**Status: single‑host proof DONE; full two‑host rig pending quota.** This plan maps the running Azure facility onto
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
