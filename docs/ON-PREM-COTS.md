# On-prem / owned hardware: what does this cost to *buy*?

**The demo runs in the cloud, but nothing about MXL requires the cloud.** The
whole facility is Linux + shared memory + software encode. This page answers the
question we got at a live demo and couldn't answer well: *"What would it cost a
station, a school, or a nonprofit to own one outright?"*

Short answer: **a capable single-box facility is ~$2,500–$4,000 of commodity
hardware** — a one-time buy a small nonprofit can rally around, versus ~$2/hr of
cloud that only makes sense when it's idle most of the time.

## What the software actually needs (measured, not guessed)

From our own Azure/AWS/GCP builds:

| Requirement | Why | Measured |
|---|---|---|
| **AVX-512 CPU** | MXL's v210 grain conversion uses it; a non-AVX box **cannot run libmxl** (it SIGILLs) | hard requirement |
| **~16–32 fast cores** | selector + keyer (CEF/HTML5) + software x264 encode + multiview compositor | full facility (2 cams, playout, keyer, multiview) idles **~10–13 of 32 cores**; 16 cores runs a leaner show |
| **64–128 GB RAM** | the domain lives in `/dev/shm` (RAM); grains are uncompressed v210 (~5 MB/frame) | 128 GB comfortable; 64 GB fine for a smaller flow count |
| **NVMe SSD** | OS + TAMS object store + container images (~7 GB) | 1 TB is plenty; TAMS recording grows with retention |
| **1 GbE NIC** | only the *encoded* program (~6 Mbps) and SRT contribution leave the box | 1 GbE is fine; **no 10 GbE or GPU needed** |
| **No GPU** | encode is x264 software; keyer is CEF (CPU) | $0 on graphics |

The single most surprising fact for buyers: **no GPU, no specialized capture
card, no SDI infrastructure.** Cameras arrive over IP (SRT/RTSP/NDI); the program
leaves over IP. It's a *computer*, not a rack of broadcast gear.

## The recommendation: three tiers

### 🟢 Tier 1 — "One-box studio" · ~$2,500 (recommended for most nonprofits)
A single commodity workstation runs the **entire** facility (contribution +
production + TAMS) — cloud "Host 1" and "Host 2" collapse onto one machine.

| Part | Suggested | ~Price |
|---|---|---|
| CPU | **AMD Ryzen 9 7950X** (16C/32T, **Zen 4 = AVX-512** ✓) | $500 |
| Motherboard | X670 / B650 (AM5) | $200 |
| RAM | 128 GB DDR5 (4×32) | $350 |
| Storage | 2 TB NVMe Gen4 | $150 |
| Cooler + case + 750W PSU | quality air or AIO | $300 |
| — subtotal (self-build) | | **~$1,500** |
| or **prebuilt** 7950X/128GB workstation | Newegg/Adamant-class | **~$2,500** |

Zen 4's AVX-512 (added in Ryzen 7000) is what makes this tier possible — it's the
[confirmed, full instruction set](https://www.phoronix.com/review/amd-zen4-avx512),
just implemented via 256-bit double-pumping (no thermal penalty). This box handles
2 cameras + playout + keyer + a modest multiview. **This is the one to rally
around.**

### 🟡 Tier 2 — "Headroom / dual-role" · ~$4,000
For heavier shows (more cameras, always-on 3×3 multiview, guests): a current-gen
**refurbished rack server**.

| Option | Spec | ~Price |
|---|---|---|
| Dell **PowerEdge R760** refurb | 2× Xeon Gold 5416S (32C total), 128 GB DDR5, NVMe | **~$5,900** ([PCSP](https://pcserverandparts.com/servers/dell-servers/poweredge-r750-server/)) |
| Dell **R650** refurb (DDR4, verify AVX-512 SKU) | 2× Xeon Gold 6330 (28C), 128 GB | **~$3,000–4,000** ([PCSP R650](https://pcserverandparts.com/dell-poweredge-r650-10-bay-sff-nvme-server-2x-intel-xeon-gold-6330-2-00-ghz-28c-128gb-ddr4-s150-software-raid-4x-3-84tb-nvme-u-2-ssd-refurbished/)) |

⚠️ **Verify the exact Xeon SKU supports AVX-512** — most Xeon Gold Scalable do,
but confirm before buying. Rack servers are loud (data-center fans) — fine for a
closet, not a control room desk.

### 🔵 Tier 3 — "True two-node" · ~$5,000
Mirror the cloud architecture exactly: a **contribution node** (cheap, in a DMZ
for stranger/guest feeds) + a **production node**, joined by the MXL fabric over a
cheap switch. Only worth it if you want the guest-isolation security posture or
plan to scale to multiple production nodes sharing one contribution ingest.

- Production: Tier 1 box (~$1,500 self-build)
- Contribution: any 8-core AVX-512 mini-PC / small workstation (~$800)
- Gigabit switch (fabric is v210 = ~1.3 Gbps/flow → **2.5GbE or 10GbE switch** if
  sending full-res uncompressed between nodes; use `pgm_lite` 540p at ~0.33 Gbps
  to stay on 1 GbE) (~$150–400)

## Cloud vs. own — the honest trade

| | **Own (Tier 1)** | **Cloud (our demo)** |
|---|---|---|
| Up-front | ~$2,500 once | $0 |
| Running | electricity (~$10–20/mo) | ~$2.11/hr **while on** |
| Break-even | — | ~1,200 hours ≈ **50 days of 24/7**, or a year of daily 3-hr shows |
| Best for | regular/daily production, fixed location | bursty events, "spin up for the game, deallocate after" |
| Scales | buy another box | change one number |

**Rule of thumb for a nonprofit:** if you produce **more than ~2–3 shows a week**,
owning a Tier 1 box pays for itself inside a year and then it's just the power
bill. If you produce a handful of events a year, cloud (deallocated between) is
cheaper. Many will want **both** — own the baseline, burst to cloud for the big
multi-camera event.

## What you do NOT need to buy (the savings story)

- ❌ **No SDI router / matrix** — sources are IP
- ❌ **No hardware switcher** (ATEM/TriCaster/Kayak) — that's what this *is*, in software
- ❌ **No GPU** — CPU encode
- ❌ **No capture cards** — SRT/RTSP/NDI in
- ❌ **No per-seat software license** — MIT-licensed open core + open EBU/BBC standards

A traditional 2-camera IP production kit (switcher + multiviewer + record) is
typically **$8,000–$25,000** of dedicated boxes. This is **one computer** running
open-source software on open standards.

## Getting started on owned hardware

1. Any Ubuntu 24.04 box that passes `grep -m1 avx512 /proc/cpuinfo`.
2. `git clone` this repo, `sudo scripts/quickstart.sh` → ON AIR in ~10 min.
3. Point cameras at it over SRT (see the SRT-latency notes in
   [`docs/GCP-BUILD-PLAN.md`](GCP-BUILD-PLAN.md) — LAN can run ~20 ms; tune up for
   WAN contribution).
4. Grow into the full facility with [`scripts/bring-up-mxl.sh`](../scripts/bring-up-mxl.sh).

*Prices are late-2026 street estimates and move around — treat them as planning
figures, not quotes. Sources linked inline. This is an independent field project;
hardware brand names are examples, not endorsements.*
