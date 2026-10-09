# Health Skin — read-only facility monitor

A second **Skin** over the same Core: a read-only page that shows what the switcher
is doing under the hood — grain flow, system load, and pipeline-writer state. It is
deliberately **separate from the control Skin** (`web/local.html`). A TD cutting a
show should see sources and a cut button, not grain internals; an engineer watching
health should not be able to touch the program bus. Core vs Skin, applied twice.

## What it shows

- **System** — CPU %, memory, 1/5/15-min load, CPU count, and the live WebRTC
  viewer count (reported for free by the encoder).
- **Grain flow** — per flow: grains/s **and unique-frames/s**. The unique-fps column
  is the one that matters: a flow can tick 30 grains/s while the *picture is frozen*
  (a repeat-wedge — head advances, frame doesn't). When that happens unique-fps
  collapses and the row goes amber (`frozen`). `dead` = probe read nothing; `slow` =
  rate below half; `ok` = both near rate.
- **Pipeline writers** — selector / keyer / encoder: running state, errors, active
  input, format, key-on, viewers, bitrate.

## Where the data comes from (and why it's cheap)

The expensive part — measuring grain rate *and* unique frames — is already done by
`tools/grain_probe.py`, which runs long-lived readers (one per flow) and writes a
rolling-window snapshot to `<thumbs>/grains.json` every 3 s. It has hard load guards
(pauses above load 26, exits above 29), so the probe can never be the thing that tips
the box over.

The Core does **not** spawn probes or `docker exec` anything. `GET /api/mxl/health`:

1. reads `grains.json` (already on disk),
2. reads `/proc` for CPU/mem/load,
3. fetches the three `:/pipeline/status` endpoints (same VM the cuts use),

then caches the aggregate for ~2 s (`MXL_HEALTH_TTL_MS`) so N open Health tabs
collapse to a single scrape. The live PGM/encode path is never touched.

## Running it

- Serve: the Core (`backend/local-server.js`) serves the page at **`/health`** and the
  data at **`/api/mxl/health`** — no extra process.
- Requires the grain probe to be running for the Grain-flow section to populate
  (`tools/grain_probe.py`, started by the facility bring-up / its runner). If the probe
  isn't running, the page still shows System + Pipeline writers and marks grains stale.

## Config

| env | default | meaning |
|-----|---------|---------|
| `MXL_GRAINS_PATH` | `<THUMBS_DIR>/grains.json` | where grain_probe writes its snapshot |
| `MXL_GRAIN_RATE` | `30` | expected grain rate (1080p30) — classifies ok/slow/frozen |
| `MXL_HEALTH_TTL_MS` | `2000` | server-side cache TTL so viewers share one scrape |

## Files

- `backend/health-info.js` — pure aggregation/classification (unit-tested, no network)
- `backend/local-server.js` — `GET /api/mxl/health` + `GET /health` (read-only)
- `web/health.html` — the page (air-gapped: self-hosted fonts, no external hosts)
- `tests/js/health-info.test.js` — 14 unit tests (frozen/dead/slow, system, pipelines)
- `tests/test_local_ui.py` — air-gap + read-only + separation-from-Core checks
