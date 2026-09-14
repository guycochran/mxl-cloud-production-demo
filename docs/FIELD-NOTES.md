# Field Notes: what an MXL v1.1.0 production deployment actually takes

This facility ran a public, operator-driven cloud switcher on MXL for IBC
week 2026 — real cameras, phone-in guests over SRT, a compositing
SuperSource, keyed graphics, TAMS record/clip, and a public WebRTC
program, with strangers pushing the buttons. This document is the honest
field report: what held, what broke, what we built around it, and where
each workaround maps to an SDK-level issue. We think this is the report
every early SDK deserves from its first production users — the sharp
edges below are offered as contribution, not complaint.

Everything here is reproducible from this repo (`QUICKSTART.md` is
validated on a fresh VM; `FINDINGS.md` has the failure taxonomy with
repro conditions).

---

## 1. What held — the measured numbers

| Property | Measured |
|---|---|
| Video path | 10-bit uncompressed v210, shared-memory domain, end to end — no compressed shortcut anywhere between ingest conform and program encode |
| Hard cut, API to selector | **20–30 ms** (100/100 soak, median 22 ms, p95 24 ms, incl. cuts fired *during* self-heal events) |
| SuperSource pane change, UI tap → compositor | **~150 ms** (57 ms tap→backend + ~90 ms long-poll push→pad flip) |
| Inter-host fabric (TCP, same VNet) | 30 grains/s sustained, avg 1080.0 slices/grain, ~1.3 Gbps per 1080p30 flow |
| Cross-network fabric (public IP, isolated VNets) | same rates, with the TargetInfo sockaddr patch (`tools/patch-target-ip.py`) |
| MXL → TAMS | continuous 1 s segmenting of the live program; frame-accurate clip export verified against burned SMPTE timecode (300 frames / 10.000 s) |
| Program integrity detector | byte-identical decoded-frame counting (YDIF==0) — see §3.4 for why rate metrics lie |

## 2. What broke — one bug wearing five costumes

Nearly every operational scar in this repo traces to a single SDK-level
behavior in MXL v1.1.0: **a reader does not survive its flow being
recreated, and a fresh attach is not always clean.** The species we
caught and characterized:

| Species | Symptom | Detection that works |
|---|---|---|
| Recreation wedge | reader silent forever after writer restart | per-branch delivery timestamps |
| Repeat-delivery wedge | reader delivers full rate **of the same grain** — every rate/head/thumbnail check stays green | unique-content counting (probe) / byte-identical decode counting (program) |
| Frozen-slices (incl. stealth variant) | fabric target's grains tick 30/s while slice payload stalls; log can stay live | `avg slices/grain` decay below 1080 |
| Stale-attach lag | fresh reader on a healthy flow delivers late or nothing for seconds-to-minutes (the "first cut to that source is a dud" experience) | first-activation content check |
| Fresh-attach-dead flow | a flow enters a state where **every** new reader gets nothing despite a live writer; only bouncing the *writer* cures it | N consecutive failed fresh attaches |

Upstream reporting: the sockaddr issue is filed (dmf-mxl#714-adjacent);
the reader-lifecycle taxonomy above is being written up from
`FINDINGS.md` for the same tracker.

## 3. The workaround catalog — labeled, mapped, and honest

We believe engineers respect *labeled* workarounds and smell hidden
ones. Every mitigation below exists because of §2, and each names the
proper fix it stands in for.

1. **Line-up warm-up** (`POST /api/mxl/warmup`): activates every
   selector slot for ~400 ms so no reader is cold when an operator
   first cuts to it. This is the software equivalent of a TD walking
   the rail before air — a ritual as old as broadcast — but we're clear
   about *why* it's needed here: stale-attach lag (§2). Proper fix:
   reader lifecycle repair upstream. It is deliberately **manual-only**:
   an earlier version rode every self-heal cascade and flashed all six
   inputs on air whenever background healing ran. Lesson learned in
   public.
2. **Repair cascade** (`POST /api/mxl/repair`): ordered rebuild of
   selector → keyer → encoder readers after any source flow recreation.
   Proper fix: readers that survive recreation.
3. **Flow stabilizer** (`tools/flow_stabilizer.py`): the architectural
   fix we designed and proved — a per-input relay that owns a stable
   flow (created once, never recreated) and swaps only its own reader
   when the volatile side churns, with a free-wheeling grid-clocked
   output. Proven in isolation (a single persistent attach rode a full
   volatile kill+recreate); rollout is scheduled for a maintenance
   window after we iterated on it too aggressively mid-show. This is
   the pattern we'd propose upstream as the reference topology for
   volatile sources.
4. **Truth-measurement doctrine**: rate, head-freshness, and thumbnail
   checks all read green through several §2 species. The only honest
   detectors we found: unique-content counting at the domain, and
   byte-identical decoded-frame counting (x264 emits skip-blocks for
   truly repeated input, so real video — which always carries sensor
   noise — never decodes byte-identical). Similarity metrics
   (mpdecimate-style) false-alarm on static scenes; we shipped that
   mistake, caught it in our own logs, and replaced it.
5. **Storm-breaker discipline**: self-heal actions are capped
   (per-branch strike files, cooldowns, log-only modes) after we
   observed compound loops — independent watchdogs amplifying each
   other into visible on-air churn. Roadmap: consolidate all reflexes
   under a single supervisor with a global action budget. This is
   ordinary control-plane maturation, and we'd rather show the journey
   than pretend it didn't happen.

## 4. Where this goes next: NMOS as the control plane

MXL gives the *data plane* (shared-memory grains, fabric between
hosts). What this facility improvised — slot lists, a bespoke REST
control API, an ad-hoc domain browser — is exactly what **AMWA NMOS**
standardizes. The forward plan:

- **IS-04 (discovery & registration):** every MXL flow in the domain
  advertised as an NMOS Node/Device/Flow/Sender in a registry. Our
  switcher's input list stops being a hardcoded array and becomes a
  standard query any broadcast controller understands. (A read-only
  IS-04 node over the domain's flow list is a weekend project — the
  domain already exposes everything needed.)
- **IS-05 (connection management) via BCP-007-03:** this is not a
  proposal — **AMWA has already published
  [BCP-007-03 "NMOS Support for MXL"](https://specs.amwa.tv/bcp-007-03/)**
  (v1.0): transport `urn:x-nmos:transport:mxl`, IS-05
  `transport_params` of `mxl_domain_id` + `mxl_flow_id` (no transport
  file), receiver capabilities per BCP-004-01, on IS-04 v1.3+/IS-05
  v1.2+. Our facility already satisfies its domain-identity
  prerequisite (`domain_def.json` with domain UUID/label/tags ships in
  our bring-up). Implementing its Sender/Receiver semantics over our
  existing flows means cuts and pane routes become standard Connection
  API patches — and **Lawo VSM, Riedel, Pebble, or any NMOS controller
  drives this switcher per published spec, not per our REST dialect**.
  That's the moment this stops being a demo and becomes a facility.
- **IS-07 (events & tally):** program/preview state as standard events
  — hardware tally lights and panels against a cloud MXL switcher.
- **IS-08 (audio mapping):** our per-input audio mixer expressed as
  standard channel mapping instead of custom fader routes.
- **Hybrid facilities:** the gateway processes (SRT ingest, WebRTC
  encode) are natural ST 2110/RTP boundary nodes. NMOS over both sides
  gives one control plane spanning on-prem 2110 islands and cloud MXL
  domains — which is precisely the EBU Dynamic Media Facility
  conversation this showcase exists to feed.

Sequenced: (1) read-only IS-04 exposure of the domain; (2) BCP-007-03
Sender/Receiver endpoints mapping `mxl_domain_id`/`mxl_flow_id`
connection patches onto the existing selector/layout API; (3) IS-07
tally from switcher state. Each step is independently demoable and none
disturbs the running data plane.

**Panel hardware today, standards tomorrow:** as a bridge before the
NMOS lane lands, the switcher's REST API is drivable from
[Bitfocus Companion](https://bitfocus.io/companion) generic-HTTP
buttons — a Stream Deck cutting an MXL cloud switcher with zero custom
code — with a proper Companion module (PGM tally as button feedback)
as the follow-on, and the BCP-007-03/NMOS controller path as the
destination.

---

*Related: [`FINDINGS.md`](FINDINGS.md) (failure taxonomy + repro),
[`QUICKSTART.md`](QUICKSTART.md) (fresh-VM validated bring-up),
[`AWS-BUILD-PLAN.md`](AWS-BUILD-PLAN.md) (two-VPC production
deployment), [`JONAS-FABRIC-HANDOFF.md`](JONAS-FABRIC-HANDOFF.md)
(receive our program as raw grains on your own machine).*
