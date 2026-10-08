<!-- SPDX-License-Identifier: Apache-2.0 -->
# AMWA IN-001 Gap Analysis — field data from a live MXL switcher

**Source spec:** [AMWA-TV/in-001](https://github.com/AMWA-TV/in-001) — *API Requirements – Control of the Media eXchange Layer (MXL) v1.0* (Draft Increment, public review open).
**Our implementation:** the Office Hours Global cloud switcher — a browser control surface driving a REST API (`/api/mxl/*`) over MXL Readers/Writers (input-selector, keyer, encoder, ingests) running live on Azure VMs. In production use with real cameras, remote contributors, and failure conditions.

**Why this is worth filing upstream:** IN-001 v1.0 defines the reader/writer control layer *in the abstract*. We have been operating almost exactly that layer under real load for weeks, and hit concrete problems the current draft does not yet address. This document maps every IN-001 requirement to what we actually built, then distills the gaps into filable issues.

> Scope honesty: our API is an application-level control surface, not a conformant IN-001 implementation. Where our operation is coarser or finer than a requirement, that difference is itself the finding. We are reviewers with field data, not claiming conformance.

---

## Part 1 — Requirement-by-requirement mapping

| IN-001 requirement | What we do | Match? |
|---|---|---|
| **5.1.1 / 5.2.1** Set/Get `MXLFlowID` (reader/writer) | Selector `input_flow_uuids[]` set on `/pipeline/start`; keyer/encoder flow IDs in their start bodies; read back via `/pipeline/status`. We set flow sets, not one ID at a time. | ◑ Partial — set/get exists but at the *pipeline* granularity, not per-reader-handle |
| **5.1.2 / 5.2.2** Set/Get `MXLDomainID`; invalid → validation error | `domain_path` in every pipeline body; a bad domain fails the start. We pass domain *paths*, not domain *UUIDs*. | ◑ Partial — see Gap A (ID vs path) |
| **5.1.3 / 5.2.3** Start reader/writer; idempotent | `/pipeline/start`. **Not idempotent in practice** — re-starting a running writer can create a second writer on a single-writer flow (we hit this live: stacked writers → conflicting grains → stutter/freeze). | ✗ Gap B — idempotency is load-bearing and we violated it |
| **5.1.4 / 5.2.4** Stop reader/writer; idempotent | `/pipeline/stop`. Stop is effectively idempotent for us. | ✓ |
| **5.1.5 / 5.2.5** Get accessible `MXLDomain`s | We do **not** expose this — domains are assumed/configured. | ✗ Gap C — no discovery endpoint |
| **6.1** FlowID created by orchestration or writer, not control | Matches us exactly — our flow UUIDs are deterministic from labels, created by the media function, not the control API. | ✓ (strong confirmation) |
| **6.2** Expose state `started`/`stopped` + transport params | We expose far more: not just started/stopped but **`live` = attached AND producing fresh content** (head age < 3 s). | ✗ Gap D — the binary state model is insufficient; this is our headline finding |
| **6.3** Structured error: code + message + optional hint | We return `{error: "..."}` — human message only, **no stable error code**, hint only sometimes. | ◑ Partial — Gap E |
| **6.4** Start/stop idempotent | See Gap B. | ✗ |
| **7.1** Consistent state reporting; no partial-update undefined states | We hit **exactly this**: a selector restarted with a subset of flows left a stale `active_input` pointing out of range → garbage on program. A partial update *did* leave an undefined-ish state. | ✗ Gap F — concrete reliability counter-example |
| **7.2** Responsive control ops | Our cut (selector active-input) is **~65 ms** end to end. Data point for "timely." | ✓ (contributes a number) |
| **7.4** Observability — "defined in a future revision" | We run Prometheus `mxl_grains_total` / `mxl_*_latency_ns` per flow + a liveness/freshness model. | → offer to inform the future revision (Gap G) |

---

## Part 2 — The gaps worth filing (ranked by leverage)

### ⭐ Gap D — "started/stopped" is not enough; readers/writers need a *liveness/freshness* state
**Requirement:** §6.2 ("Readers/writers shall expose `started` / `stopped`").
**Field finding:** a writer can be `started` and attached and still be **producing nothing new** — the flow's grain head stops advancing while the handle reports healthy. We learned this the hard way: fabric targets create guest flows at boot, so an **idle guest slot with nobody connected reported `started`/"receiving."** We had to add our own signal — `live = attached AND head-age < 3 s` — because "is the writer started?" and "is fresh content actually arriving?" are different questions. Worse: aggregate freshness lies too (a 4-up composite with 3 moving quadrants masks 1 frozen one), so freshness must be judged on *content uniqueness per source*, not pipeline liveness.
**Proposed:** §6.2 should require exposing a content-liveness indicator distinct from operational state — minimally a last-grain / head-advance timestamp per flow, so a controller can tell "started but stale" from "started and flowing." This is the single most valuable thing we'd add.

### ⭐ Gap B — idempotency of Start must be specified against the *single-writer* invariant
**Requirement:** §5.1.3 / §5.2.3 / §6.4 ("Starting an already running reader/writer shall be idempotent").
**Field finding:** in our implementation, calling start again spawned a **second writer** on a single-writer flow. Two writers on one flow = readers catch conflicting grains = visible stutter/freeze/timecode-jump. "Idempotent" is doing heavy lifting here and is easy to violate.
**Proposed:** the spec should state that Start is idempotent *with respect to the underlying reader/writer instance* (identity-based), not merely "returns 200," and that an implementation MUST NOT create a second writer for a flow that already has one. Naming the single-writer invariant explicitly would prevent the exact class of bug we hit.

### Gap F — "no partial-update undefined states" (§7.1) needs an example + an atomicity hint
**Field finding:** restarting the selector with a subset of the configured flows left `active_input` pointing at an index that no longer existed → undefined output (garbage/pattern on program) until a full rebuild. A partial reconfiguration genuinely produced an undefined state.
**Proposed:** §7.1 would be stronger with (a) a worked example of a partial-update hazard, and (b) guidance that reconfiguring a reader's flow set should either be atomic or re-validate dependent pointers (e.g. active input) against the new set. We can contribute the example.

### Gap C — no "accessible domains" discovery in our impl; is the requirement reader/writer-local or system-wide?
**Requirement:** §5.1.5 / §5.2.5.
**Field finding + question:** we never needed per-reader domain discovery because domains are statically mapped at deploy. But in a multi-host fabric (our VM1↔VM2 case), "which domains can this writer reach" is genuinely dynamic once a fabric proxy is in play. **Question for the WG:** is 5.1.5/5.2.5 meant to reflect static filesystem mounts only, or also dynamically-reachable remote domains (e.g. via a fabrics proxy)? The answer changes whether this is a cheap getter or a live discovery.

### Gap E — structured errors need a *stable enumerated code*, not just a message
**Requirement:** §6.3.
**Field finding:** human messages alone meant our control UI couldn't branch on error *type* (e.g. "switcher busy / self-heal in progress" vs "input has no feed" vs "invalid domain"). We ended up string-matching messages — brittle.
**Proposed:** §6.3 should require the error **code** be a stable, enumerated value (not free text), so controllers can react programmatically. Optional: a small starter enum (invalid-flow-id, invalid-domain, not-running, busy, no-content).

### Gap A — Domain is addressed by *path* in practice, but the spec queries Domain*ID* (UUID)
**Requirement:** §2.1 / §5.x (query `MXLDomainID`), §2.2 (ID→path mapping explicitly out of scope).
**Field finding:** every real operation we do carries a domain **path** (`/dev/shm/mxl/domain_1`), not a UUID. §4 assumes the media function can resolve ID↔path, but in our live system the control layer only ever sees paths; the UUID never surfaces. **Observation for the WG:** if the ID↔path mapping is truly out of scope *and* real functions operate on paths, controllers may never obtain the `MXLDomainID` the API is specified to query. Worth a sentence on where the UUID is expected to come from.

### Gap G — Observability (§7.4, "future revision"): offer our running model as input
We already run per-flow Prometheus metrics (`mxl_grains_total`, source/network latency quantiles) plus the freshness model from Gap D. Happy to contribute these as a concrete starting point when §7.4 is drafted.

---

## Part 3 — Draft GitHub issues (file these on AMWA-TV/in-001)

**Recommended: file the top 2–3. Issue 1 (Gap D) is the one to file first — it's our strongest, most spec-relevant finding.**

### Issue 1 — §6.2: distinguish operational state (`started`) from content liveness (is fresh media flowing?)
> **Context:** field feedback from a live MXL switcher in production.
> §6.2 requires readers/writers to expose `started`/`stopped`. In our live deployment a reader/writer can be `started` and attached yet produce no *new* content — the grain head stops advancing while the handle still reports healthy. Concretely: fabric targets create flows at boot, so an idle input (no contributor connected) reported `started`/"receiving," which misled the controller. We had to add a separate signal — attached AND head-age < 3 s — and judge it per-flow (aggregate freshness masks one frozen source among several).
> **Suggestion:** require exposing a content-liveness indicator distinct from operational state — minimally a last-grain/head-advance timestamp per flow — so a controller can distinguish "started but stale" from "started and flowing." Happy to share our implementation and the failure case.

### Issue 2 — §5.1.3/5.2.3/6.4: specify Start idempotency against the single-writer invariant
> §6.4 requires Start to be idempotent. In our implementation a second Start created a *second writer* on a single-writer flow; two writers on one flow produced conflicting grains and visible artifacts (stutter/freeze/timecode jump).
> **Suggestion:** state that Start is idempotent with respect to the reader/writer *instance* (identity-based), and that an implementation MUST NOT create a second writer for a flow that already has one. Naming the single-writer invariant would prevent this class of bug.

### Issue 3 — §7.1: add a partial-update example + atomicity/revalidation guidance
> §7.1 requires that partial updates not leave readers/writers in undefined states. We hit a concrete case: reconfiguring a selector-style reader with a *subset* of its prior flows left a stale active-input pointer out of range → undefined output until a full rebuild.
> **Suggestion:** add a worked example and guidance that reconfiguring a flow set should be atomic or re-validate dependent references against the new set. We can contribute the example.

### (Optional) Issue 4 — §6.3: require a stable enumerated error code
> §6.3 specifies code + message + optional hint. For controllers to react programmatically, the *code* needs to be a stable enumerated value rather than free text — otherwise clients string-match messages (brittle). Suggest a small starter enum (invalid-flow-id, invalid-domain, not-running, busy, no-content).

---

## Part 4 — The "thin-glue" caveat (our own guardrail)

Per the fabrics-proxy author and IN-001 itself, this control/discovery surface **will change**. Our own API is therefore kept as a swappable *adapter*, not a bespoke orchestration layer — so IN-001 / NMOS / JT-DMF control can drop in as they solidify. We are filing these issues to help the standard converge, not to entrench our current shape. Filing early, while IN-001 is a Draft Increment soliciting review, is exactly when field data has the most leverage.

---

*Prepared from the live switcher's real `/api/mxl/*` behavior and documented failure cases (see `MXL-NOTES.md`). Companion to `control.html` (the ops→IN-001 control matrix).*
