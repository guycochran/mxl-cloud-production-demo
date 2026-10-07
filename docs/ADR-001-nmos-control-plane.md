<!-- SPDX-FileCopyrightText: 2026 Contributors to the Media eXchange Layer project. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ADR-001: NMOS owns connection control; we own the media engine

- **Status:** Accepted
- **Date:** 2026-10-08
- **Decision owners:** mxl-cloud-production-demo maintainers
- **Supersedes / relates to:** `docs/ARCHITECTURE-CORE-AND-SKINS.md`,
  `docs/ROADMAP-FROM-SPECS-2026-10.md`, `docs/FINDINGS.md` §6 (reader-wedge)

## Context

Two signals from people who define this space prompted a deliberate architectural
decision rather than a drift:

1. **cbcrc/mxl-hands-on#34** (our report: "readers wedge silently when a source flow is
   recreated; status APIs still say running"). The cbcrc maintainer closed it as
   wontfix-for-the-test-apps: *"The right solution for this will be to embrace NMOS when it
   is going to be available."* He pointed at `garethsb/mxl-nmos-c-example`.
2. **`garethsb/mxl-nmos-c-example`** (Gareth Sylvester-Bradley, NVIDIA/AMWA) — a producer
   Node + consumer Node where the **consumer opens MXL flows only after an IS-05 activation**,
   carrying `{mxl_flow_id, mxl_domain_id}` in the IS-05 `transport_params`, with disable/
   re-enable. It is a *connection* demo: it contains no switcher — no selector, keyer, mixer,
   restamp, or contribution ingress.

The tempting but wrong framing is "build the switcher with NMOS **instead of** what we're
doing." That treats a control protocol and a media application as alternatives. They are
different layers. NvNmos + Gareth's example give you zero switching; our engine is not a
connection router. The question is not either/or — it is *which layer owns what*.

## Decision

**Build and own the media engine. Delegate connection control to NMOS. Do not reinvent the
data plane or the control protocol.**

Layer ownership:

| Layer | What it is | Owner |
|---|---|---|
| Data plane | grains in shared memory (`mxlsink`/`mxlsrc`) | **MXL SDK** — consume, never reinvent |
| **Media engine** | select / cut / key / mix / restamp / conform; program & preview | **This project** — the differentiated, unowned work |
| **Contribution ingress** | SRT/RTSP guests → MXL, restamped onto the local grain clock (IN-005) | **This project** |
| **Connection control** | discover flows; route Sender→Receiver; re-open on recreation | **NMOS** (IS-04 discovery, IS-05 connection) |
| Timed metadata | ANC / captions / SCTE as event flows | **MXL v1.2** (dmf-mxl#327) — consume when it lands |

Corollaries:
- **The media engine is the product.** No one else in the ecosystem is building the open
  software switcher; the connection demos and the SDK stop at the boundaries of our layer.
  This is our positioning and we invest here.
- **Connection control converges to NMOS.** Our REST cut API and the `/api/mxl/repair`
  cascade are the *operational* form of what IS-05 standardizes. We promote NMOS from a
  side "shim" to the **primary** routing/connection interface over time.
- **The reader-wedge (#34) proper fix is NMOS re-activation, not stat-polling.** A reader
  re-opens a recreated flow through an IS-05 (re)activation — the pattern Gareth's consumer
  demonstrates — rather than holding a stale handle. We adopt this once a controller is in
  the loop; until then the repair cascade remains the pragmatic patch.

## What we explicitly will NOT do

- **Not** reimplement the MXL shared-memory data plane.
- **Not** hand-roll a bespoke connection/routing protocol when IS-04/IS-05 exist. (This is
  exactly what #34's closure warned against; we take the hint.)
- **Not** throw away the media engine to "adopt NMOS." NMOS has no media engine to adopt.
- **Not** hard-depend on NvNmos / `nvnmosd`. Gareth's repo and `gst-nmos-rs` are C-API
  *references*; our node stays a lightweight BCP-007-03 implementation. We copy the **wire
  format** (standard IS-05), not the daemon.

## Convergence steps (how we get from here to the decision)

Current state: `tools/nmos_node.py` already does IS-04 discovery + an IS-05 `connection/v1.1`
shim whose receiver `/staged` activation drives a real selector cut (PR #37).

To mature it toward the reference, in priority order:
1. **Carry flow identity in `transport_params`**, not just IS-04 tags:
   `transport_params: [{ "mxl_flow_id": "<uuid>", "mxl_domain_id": "<uuid|null>" }]`.
2. **Bump Connection API to v1.2** (the version `connect.sh` in the reference speaks).
3. **Add Sender/Receiver disable + re-enable** (IS-05 `master_enable` false→true cycle).
4. **Advertise the domain UUID** as `mxl_domain_id` (from `domain_def.json`; BCP-007-03 wants
   a UUID here too).
5. **Reader re-open via re-activation** (the #34 fix) — wire `flow_stabilizer` / the repair
   path to re-drive IS-05 activation on flow recreation, once a controller drives activations.

None of these touch the media engine; they are all in the control-plane layer, which is the
point of this ADR.

## Consequences

- **Positive:** we invest effort where we are differentiated (media engine, contribution
  seam) and standards-compliant where the ecosystem already converged (NMOS control). We
  align with the maintainers who define the space instead of diverging from them.
- **Positive:** the reader-wedge gets an architecturally-correct fix path, not just a patch.
- **Cost:** maturing the NMOS node is real work (the 5 steps above), and full reader-reopen
  needs a controller in the loop — deferred until that exists.
- **Risk accepted:** until the convergence steps land, our connection control is non-standard
  (REST + repair cascade). That is a known, bounded gap with a written plan to close it — not
  a drift.
