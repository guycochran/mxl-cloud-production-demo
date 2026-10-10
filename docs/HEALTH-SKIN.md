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

The grain/frozen measurement is **already being produced** on a running facility:
`tools/mxl_thumbs.py` (the multiview thumbnail generator) also writes `health.json`
beside the thumbnails, carrying per-slot `{age, frozen}` — `age` = seconds since the
last frame, `frozen` = seconds since the content last *changed* (its own repeat-wedge
detector). Plus system load/mem and viewers. Because the thumbnail probe is already
running, the Health Skin adds **no new process and no second set of MXL readers**.

`GET /api/mxl/health`:

1. reads `health.json` (already on disk; falls back to `grains.json` if you run
   `tools/grain_probe.py` instead — that probe adds unique-fps and has hard load
   guards: pauses above load 26, exits above 29),
2. uses the probe's host-true load/mem/viewers (so it's correct even when the Core
   runs off-box) and local `/proc` for CPU %,
3. fetches the three `:/pipeline/status` endpoints (same VM the cuts use),

then caches the aggregate for ~2 s (`MXL_HEALTH_TTL_MS`) so N open Health tabs
collapse to a single scrape. The Core never spawns a probe or `docker exec`s anything,
and the live PGM/encode path is never touched.

> Note: `mxl_thumbs.py`'s `health.json` is rewritten on a ~15 s cadence (the per-slot
> age/frozen values inside it are sub-2 s fresh). For a tighter live refresh, run
> `tools/grain_probe.py` (3 s, adds unique-fps) — the endpoint picks up `grains.json`
> automatically.

## Running it

- Serve: the Core (`backend/local-server.js`) serves the page at **`/health`** and the
  data at **`/api/mxl/health`** — no extra process.
- The Grain-flow section populates from whichever probe snapshot exists:
  `health.json` (written by the already-running `tools/mxl_thumbs.py`) is used first;
  `grains.json` (from `tools/grain_probe.py`) is the fallback. If neither is present the
  page still shows System + Pipeline writers and marks grains stale.

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
