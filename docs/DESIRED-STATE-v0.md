# Desired-State Controller v0.1 — design sketch (report-only)

**Status:** DRAFT design / sketch. Not built against a live facility.
**Facility placeholder:** `MXL Lab` (generic; no site-specific names).
**Hard rule:** AI / automation talks only to `/api/mxl/v1` — never to shell,
docker, SSH, or process kill. This doc sketches *what* a controller would
observe and (later) repair; it does not authorize live repair.

Stacks conceptually **after** `config/facility.json` (desired topology) and
`scripts/mxl-doctor` (read-only health). Depends on clean facility identifiers
(#26 / #27) so desired-state keys stay portable placeholders, not real VM /
RG / IP literals.

---

## 1. What this is (and is not)

| Is | Is not |
|---|---|
| A **report** that compares desired vs actual per function | Auto-heal, container restart, or process kill |
| A sketch of how `facility.json` becomes the **desired-state** source | A live control loop on any lab VM |
| Three orthogonal actual planes from day one: **process / media / dependency** | Treating `process=running` as healthy |
| A future optional `repair <function>` verb (Guy-gated) | Permission to run scratch-VM kill experiments yet |
| Generic wording for any MXL Lab adopter | Site skins, vendor product internals, cloud project names |

**v0.1 ships report-only.** Optional repair is documented as a later phase and
stays off until Guy approves a scratch-VM experiment.

---

## 2. Three planes — process ≠ media ≠ dependency

A process that is **running** is not enough. The interesting DMF-class failure
is **alive process / dead media**: the container or writer is up, the flow UUID
may even be registered, but grains are not advancing (`unique_fps = 0`).

From day one the model separates:

| Plane | What it answers | Example signals |
|---|---|---|
| **Process** | Is the writer / container / control port alive? | `running` / `stopped` / `missing` |
| **Media** | Is essence actually moving? | `flow` present?, `cadence` fps, `unique_fps` |
| **Dependency** | Are upstream inputs and downstream consumers in the expected topology? | `upstream`, `downstream` |

**Drift rule (v0.1):** a function is `HEALTHY` only when **all** planes match
desired. `process=running` with `unique_fps=0` is **DRIFT** (dead media), not ok.

```
                    ┌─────────────┐
   upstream ───────▶│  function   │───────▶ downstream
                    │ process     │
                    │ media       │
                    └─────────────┘
```

---

## 3. `facility.json` becomes desired state

Today `config/facility.json` is the single source of truth for domain path,
network placeholders, control ports, and flow UUIDs. Desired-state v0 treats
that manifest as the **DESIRED** column:

- Each **function** is a named capability the lab must hold (e.g. `selector`,
  `encoder`, `pgm_audio`, `cam_ingest`, `keyer`).
- Desired facts come from the manifest (ports, flow roles, program path) plus
  a small optional `desired_state` section (sketch below) for recovery timing,
  fail policy, and per-plane expectations — additive, not required for today's
  consumers.
- **ACTUAL** comes from a separate snapshot (doctor output, `/api/mxl/v1`
  state, or a mock JSON in CI), already split into process / media /
  dependency fields. The report never mutates the facility.

### 3a. Additive sketch (optional keys — not wired yet)

```json
{
  "facility": { "name": "MXL Lab" },
  "desired_state": {
    "version": "0.1",
    "mode": "report_only",
    "functions": {
      "keyer": {
        "role": "program_graphics",
        "control_port_key": "keyer",
        "video_flow_key": "keyer",
        "process": "running",
        "media": { "flow": "present", "cadence_fps": 30, "unique_fps_min": 1 },
        "deps": { "upstream": ["selector"], "downstream": ["encoder"] },
        "recovery": { "T0_ms": 0, "T1_ms": 500, "T2_ms": 2000, "T3_ms": 5000, "T4_ms": 15000, "T5_ms": 60000 },
        "fail_policy": "hold_last"
      },
      "pgm_audio": {
        "role": "program_audio",
        "audio_flow_key": "pgm",
        "process": "running",
        "media": { "flow": "present", "cadence_fps": 30, "unique_fps_min": 1 },
        "deps": { "upstream": ["guest1_audio", "voice"], "downstream": ["encoder"] },
        "recovery": { "T0_ms": 0, "T1_ms": 200, "T2_ms": 1000, "T3_ms": 3000, "T4_ms": 10000, "T5_ms": 30000 },
        "fail_policy": "audio_bypass"
      }
    }
  }
}
```

Until that section exists, the report stub derives a minimal function list
from `control_api.ports` + `program` keys so CI works against today's
manifest, and reads per-plane actuals from the mock snapshot.

---

## 4. Drift table format

Human-readable, one row per function. v0.1 columns (fixed):

| Column | Plane | Meaning |
|---|---|---|
| **FUNCTION** | — | Stable function id |
| **DESIRED** | desired | Short policy summary (process + media floors + fail_policy) |
| **PROCESS** | process | `running` / `stopped` / `missing` |
| **FLOW** | media | `present` / `absent` |
| **CADENCE** | media | Observed fps (grain cadence) |
| **UNIQUE_FPS** | media | Unique-frames-per-second (liveness; `0` = dead media) |
| **UPSTREAM** | dependency | Expected inputs ok / broken |
| **DOWNSTREAM** | dependency | Expected consumers ok / broken |
| **DRIFT** | — | `ok` or `DRIFT` |

### 4a. Sample — HEALTHY (all planes green)

```
FUNCTION   DESIRED                          PROCESS  FLOW     CADENCE  UNIQUE_FPS  UPSTREAM        DOWNSTREAM   DRIFT
---------  -------------------------------  -------  -------  -------  ----------  --------------  -----------  -----
selector   running · flow · ≥1 ufps · 30    running  present  30       30          cam,playout     keyer        ok
keyer      running · flow · ≥1 ufps · 30    running  present  30       30          selector        encoder      ok
encoder    running · flow · ≥1 ufps · 30    running  present  30       30          keyer,pgm_audio egress       ok
pgm_audio  running · bypass ok · ≥1 ufps    running  present  30       30          guests,voice    encoder      ok
```

### 4b. Sample — classic dead-media drift (`unique_fps=0`)

Process alive, flow registered, cadence may still tick (or freeze) — but
**unique_fps = 0** means no new essence. That is DRIFT even though PROCESS
says `running`:

```
FUNCTION   DESIRED                          PROCESS  FLOW     CADENCE  UNIQUE_FPS  UPSTREAM   DOWNSTREAM   DRIFT
---------  -------------------------------  -------  -------  -------  ----------  ---------  -----------  -----
keyer      running · flow · ≥1 ufps · 30    running  present  30       0           selector   encoder      DRIFT
```

This is the failure mode a process-only check misses.

Machine-readable twin: JSON array of row objects with the same fields
(`function`, `desired`, `process`, `flow`, `cadence`, `unique_fps`,
`upstream`, `downstream`, `drift`) for tests and future UI. The stub prints
the table to stdout and can emit `--json`.

---

## 5. Phasing

### Phase A — report-only (this PR / v0.1)

1. Load desired facts from `config/facility.json`.
2. Load actual facts from a **mocked** actual-state JSON whose `functions`
   entries already carry process / media / dependency fields.
3. Print the multi-plane drift table. Exit non-zero if any `DRIFT`.
4. **No** docker, **no** heal, **no** SSH, **no** process signals.

### Phase B — optional `repair <function>` (not approved yet)

- Explicit verb, one function at a time: `desired-state repair keyer`.
- Implementation must go through **`/api/mxl/v1`** ops/repair surfaces only
  (never shell). Exact routes TBD with the v1 contract.
- Repair choice should consider *which plane* failed (restart process ≠
  re-attach flow ≠ fix upstream).
- Gated: default remains report-only; repair requires an env flag **and**
  operator confirmation.
- **Blocked** until Guy approves a scratch-VM kill / recover experiment.

---

## 6. Recovery timing fields (T0–T5)

Per-function budgets for *observing* recovery, not for auto-acting in v0.1.
Values are illustrative placeholders for MXL Lab; tune per site later.

| Field | Intent (sketch) |
|---|---|
| **T0** | Detect / sample instant (report tick) |
| **T1** | Soft warn — function unhealthy but within grace |
| **T2** | Hard warn — sustained drift; page / log escalate |
| **T3** | Candidate for manual or gated repair consideration |
| **T4** | Escalate: human required if still drifting |
| **T5** | Give-up / fail-closed boundary for that function |

The report may *display* which band the current drift age falls into when
the actual snapshot includes `since_ms`. It must not trigger repair from
crossing a T-band in v0.1. Dead-media (`unique_fps=0`) should typically
enter T1/T2 faster than a clean process-stop, because program looks fine
until someone notices the freeze.

---

## 7. `fail_policy` sketch — audio bypass

Some functions should degrade instead of hard-stopping program.

| Policy | Typical function | Behavior (sketch) |
|---|---|---|
| `hold_last` | video selector / keyer | Keep last good output; mark DRIFT on media plane |
| `audio_bypass` | `pgm_audio` | If program mix media is unhealthy, pass through a designated bypass source so egress stays alive |
| `fail_closed` | encoder / egress | Stop or freeze rather than emit wrong essence |
| `ignore` | parked / optional ports | Never count as DRIFT when parked |

**Audio bypass (detail):** desired state names a bypass input (flow key or
v1 source id). Actual state reports whether bypass is inactive, armed, or
active. Drift rules:

- Desired `fail_policy=audio_bypass` + mix media down + bypass **active** →
  process/media may show degraded but overall `ok` (policy-honoring).
- Mix media down + bypass **inactive** → `DRIFT`.
- Mix media up + bypass active → `DRIFT` (unexpected bypass).

No auto-engagement of bypass in v0.1 — report only.

---

## 8. Hard rule — AI talks only to `/api/mxl/v1`

Agents, assistants, and automation **must not**:

- run shell / SSH on the lab host,
- `docker kill` / `docker restart`,
- signal or rewrite processes,
- edit live `facility.json` on a running box.

They **may** (when implemented and authorized):

- `GET` v1 read surfaces (`/api/mxl/v1/meta`, `/state`, `/sources`, …),
- call documented v1 control/ops endpoints the core exposes,
- consume this report’s JSON (all three planes).

If a capability is missing from v1, the answer is “extend the contract,”
not “reach around with shell.” See the v1 contract sketch and
`scripts/mxl-doctor` (read-only default) for the same safety split:
inspect is cheap and portable; mutate is explicit and gated.

---

## 9. Stub in this PR

| Path | Role |
|---|---|
| `tools/desired_state_report.py` | Read manifest + mocked actual JSON; print multi-plane drift table |
| `tests/fixtures/actual_state_mock.json` | Tiny actual-state fixture (includes keyer `unique_fps=0`) |
| `tests/test_desired_state_report.py` | Assert HEALTHY columns + dead-media drift (no docker) |

Run locally:

```bash
python3 tools/desired_state_report.py \
  --facility config/facility.json \
  --actual tests/fixtures/actual_state_mock.json
```

---

## 10. Out of scope / non-goals

- Live facility touch, scratch-VM kill experiments, auto-repair.
- Site-specific product names, skins, cloud project IDs, real VM IP / RG / name.
- Replacing `mxl-doctor` — doctor remains the health front door; this sketch
  is the desired-vs-actual **policy** layer on top (doctor may later feed the
  media-plane numbers such as `unique_fps`).
- Full schema for `desired_state` in `facility.schema.json` (follow-up once
  the shape stabilizes).

---

## 11. Open questions

1. Function inventory: derive only from `control_api.ports`, or maintain an
   explicit allowlist under `desired_state.functions`?
2. Should `/api/mxl/v1` grow a `GET /v1/desired-drift` that returns this table
   (all planes), or stay a CLI/doctor companion forever?
3. T-band defaults: global lab profile vs per-function overrides only?
4. When repair lands, is the unit of repair a function id, a v1 source id,
   or a doctor “leg” name — and how do we keep one vocabulary?
5. Cadence vs unique_fps: treat cadence-nonzero / unique_fps-zero as its own
   DRIFT subclass in the JSON (`drift_reason: dead_media`) for UI badges?
