<!-- SPDX-License-Identifier: Apache-2.0 -->
# Architecture note: core ("bones") and skins

**Status:** DRAFT proposal for discussion — nothing here is implemented yet. Written against `master` @ `ab44ddc` plus the open drafts it refers to (#17, #19, #20, #23, and the two UI mockups in #18 / #21).
**Audience:** the maintainer, the other coder, and anyone building on the switcher.

## 1. Why

The repo now has one real switcher UI (`web/local.html`), two design mockups of very different shapes (a broadcast-panel layout in #18 and a director "cockpit" in #21), and at least one downstream user who needs organisation-specific behaviour. Without a boundary, every new need either forks the UI or leaks specifics into the open code. The proposal is to treat the open repo as stable **bones** (sources, slots, layouts, preview/take, control API) and everything visible or organisation-specific as replaceable **skins** and **private downstream code**.

## 2. Principles

1. **Core is organisation-neutral.** No customer, event, vendor-specific or credential-bearing code in this repo. Anything that only makes sense for one deployment lives downstream.
2. **One contract.** Skins and downstream code talk to the core only through a **versioned HTTP API** (plus the program video, see §4). No skin reads the MXL domain, hits `:960x` media-function ports, or imports backend code.
3. **Fixes go to core first.** A defect found downstream is fixed in this repo, released, and then the downstream project bumps its pin. Downstream carries patches only as short-lived, upstream-bound exceptions.
4. **Boring compatibility.** Additive changes are free; breaking changes need a new major API version and an overlap period.
5. **Runs unattended on one box.** The single-box quickstart stays the reference path; every API feature must work there with generated config (see #19) — no dependence on the lab's fixed UUIDs.
6. **Safe by default.** Localhost bind, optional token auth on every mutating route, no external hosts in any shipped UI (air-gap rule already enforced by `tests/test_local_ui.py`).

## 3. Proposed boundary

| Layer | Lives in | Contents |
|---|---|---|
| **Media plane** | core | MXL domain, containers, `tools/` ingest/stabilizer/multiview, `scripts/quickstart.sh`, self-healer (#20) |
| **Core control plane** | core | `backend/` (`mxl-routes.js`, `local-server.js`, `mxl-auth.js`, `facility.js`), facility manifest (generated, #19), the **v1 API** (§5) |
| **Skins** | core (examples) or downstream | Static UI bundles that call the v1 API (§6). `web/local.html` = `classic`; #18 → `broadcast`; #21 → `cockpit` |
| **Adapters / bridges** | downstream | Anything that turns an outside system (a hand-raise queue, a hardware layout controller, a chat tool) into calls on the v1 API or into a skin's data feed |
| **Deployment** | downstream | Domain names, tunnels, VM sizes, tokens, branding, event schedules |

Rule of thumb: if a user of the quickstart on a fresh VM would not need it, it is not core.

## 4. What counts as "the API"

- **Control/data API** — JSON over HTTP under `/api/mxl/v1/`, plus an events stream (§5.3). This is the versioned contract.
- **Thumbnails** — `GET /api/mxl/v1/thumbs/:id.jpg` (images, same version).
- **Program video** — the WebRTC/WHEP page and proxy (`/program`, `/mxl2webrtc/*`) is a *media* endpoint. It is part of the deployment, not the JSON contract; a skin may embed it but must not assume anything beyond "an embeddable program view at a configured URL" (exposed via `GET /v1/meta`).

## 5. API inventory

### 5.1 Existing routes (read from `master` + open drafts) and their v1 fate

| Route today | Auth | Notes | v1 proposal |
|---|---|---|---|
| `GET /api/mxl/status` | open | `{input, pvw, key, busy, slots[{slot,role,flow,live,pgm,pvw}], patterns:{tg1}, available[]}` | Keep, as `GET /v1/state`. Drop the `patterns.tg1` naming (leaks an internal process name). `live` currently means "wired into the selector", not "has fresh video" (see 5.2). |
| `GET /api/mxl/slots` (in `local-server.js`) | open | `{slots[{slot,role,label}]}` from the manifest | Merge into `/v1/sources`; add stable `id`, `kind`, `capabilities`. |
| `GET /api/mxl/thumbs/:name` | open | JPEG per source, path-traversal guarded, 404 = no signal | Keep as `/v1/thumbs/:id.jpg`; address by stable id, not file name. |
| `POST /api/mxl/preview {input}` | token | server-side PVW only | Keep; body uses source `id`. |
| `POST /api/mxl/take` | token | cut PVW → PGM | Keep. Transitions are out of scope for v1 (hard cut only). |
| `POST /api/mxl/input {input}` | token | direct cut; accepts role name / `cam` / `cam2` / index | Keep as `/v1/cut {source}`; accept **only** stable ids (strict validation exists after #17). |
| `POST /api/mxl/key {on}` | token | graphics key on/off | Keep. |
| `POST /api/mxl/pattern {pattern}` | token | sets the test generator; after #17 it no longer cuts | Move under a `test-source` capability; optional. |
| `POST /api/mxl/warmup` | token | full reader sweep, heavy | **Not** in v1. Operational; keep as an unversioned `/ops/` route. |
| `POST /api/mxl/repair {slot,key}` | token + rate limit | rebuilds the cascade | **Not** in the v1 contract (operational). Skins may call it only via an `ops.repair` capability. |
| `GET /program`, `/mxl2webrtc/*` | open | WHEP proxy | Media plane (§4). |

Cross-cutting facts today: error bodies are `{error: "<string>"}` with inconsistent status codes (400/409/502; 504 after #17); token via `Authorization: Bearer` or `X-MXL-Token`; 401 / 429 (#23) / 503 when unconfigured; no CORS policy; no version in the path; no API-level capability discovery.

### 5.2 Missing for a real contract

| Gap | Today | Proposal |
|---|---|---|
| **Version + capability discovery** | none | `GET /v1/meta` → `{api_version, core_version, capabilities[], program_url, auth:{required, scopes}}`. |
| **Stable source ids** | slot **index** and role names; the manifest roles are inconsistent (see review notes R3), and generated manifests (#19) differ per box | Every source has an opaque stable `id`, a `label`, and `kind` (`camera`, `guest`, `playout`, `generator`, `layout`, `other`). Indices are display order only. |
| **Source health** | `live` = wired; `tools/mxl_thumbs.py` already writes a `health.json` (no-signal, repeat-wedged detection) but nothing serves it | `health` per source: `{state: ok\|no_signal\|stale\|wedged, last_frame_age_ms}` in `/v1/sources` and the event stream. |
| **Candidates list (queue-agnostic)** | none in the open core | Generic **candidates**: people/sources that *could* go on air next, `{id, label, state: waiting\|queued\|ready\|on_air, order, source_id?, meta{}}`. Core stores/serves them; **who populates them is a downstream concern** (a bridge posts to `POST /v1/candidates`). Skins render them; the cockpit mock (#21) is the first consumer. Needs a decision on persistence (memory vs file) — see §11. |
| **Speaking / audio levels** | audio mixer exists (`tools/audio_pgm.py`) but level data and mixer state come from a backend route (`/api/mxl/audio-state`) that is not shipped in this repo | `GET /v1/audio` (per-input level + mute + gain) and levels in the events stream. Requires a small level meter in the audio tool. |
| **Events stream** | skins poll `/status` (~1 s) and thumbs (~1.5 s) | `GET /v1/events` — **SSE** (works through a tunnel/proxy, no extra dependency): `state`, `source.health`, `candidates`, `audio.levels`, `layout`. Polling stays as the fallback; every event is also obtainable by GET. |
| **Layout presets API** | the layout compositor reads its commands from a backend route (`/api/mxl/layout-state`) that is not shipped in this repo; mockups hard-code presets | `GET /v1/layouts` (preset list with box geometry as canvas fractions, box count) and `POST /v1/layout {preset, assignments:[{box, source}]}`; a layout is also a selectable source (`kind: layout`). The open core needs its own small layout-state endpoint feeding `layout_pgm.py`. |
| **Auth scopes** | one shared token for all mutating routes; reads open | At least `read` vs `control` vs `ops`; reads optionally token-protected (`MXL_REQUIRE_READ_AUTH`). Per-skin or per-operator tokens are future work. |
| **Error model** | ad-hoc | `{error:{code,message}}` with stable codes (`unknown_source`, `source_not_attached`, `busy`, `selector_down`, `rate_limited`, `unauthorized`, `timeout`). |
| **Idempotency / sequencing** | `busy` lock → 409 | Cut/take return the new state; optional `request_id`; document that clients must treat 409 `busy` as retryable. |
| **CORS / embedding** | none | Same-origin by default; configurable allowed origins for external skins. |

### 5.3 Event stream sketch

```
GET /api/mxl/v1/events        (Accept: text/event-stream)
event: state          data: {"pgm":"src_ab12","pvw":"src_cd34","key":true,"busy":false}
event: source.health  data: {"id":"src_ab12","state":"ok","last_frame_age_ms":34}
event: audio.levels   data: {"src_ab12":-18.2,"src_cd34":-60.0}      (throttled, e.g. 5 Hz)
event: candidates     data: {"items":[...]}
event: layout         data: {"preset":"2up","boxes":[...]}
```

## 6. Skin contract

A **skin** is a static bundle that renders the switcher using only the v1 API.

### 6.1 Layout and manifest

```
web/skins/<skin-id>/
  skin.json        # manifest (below)
  index.html       # entry point
  ...assets        # self-hosted only (fonts, images, css, js)
```

```json
{
  "id": "broadcast",
  "name": "Broadcast panel",
  "version": "1.0.0",
  "api": "^1.0",
  "entry": "index.html",
  "capabilities": ["state.read", "events.read", "thumbs.read",
                   "control.preview", "control.take", "control.cut", "control.key"],
  "optional_capabilities": ["audio.read", "candidates.read", "layouts.control"],
  "viewport": {"min_width": 1024, "touch": false},
  "preview_image": "preview.png"
}
```

- `api` is the semver range of the core API the skin needs. (Manifests are strict JSON; no comments.)
- **Capabilities** are declared, not self-granted: the core's `/v1/meta` lists what the running instance offers. A skin loads only if its required capabilities are present (otherwise the loader shows a clear "this core lacks X" page). Optional ones degrade gracefully.
- **Existing UIs become the first skins:** `web/local.html` → `classic` (default), #18's `web/local-broadcast.html` → `broadcast`, #21's `web/td-cockpit.html` → `cockpit`. #21 is currently a no-network mock; making it a real skin means wiring it to `candidates`, `audio` and `layouts`, which are the gaps in §5.2.

### 6.2 Loading and selection

- `local-server.js` serves `/skins/<id>/…` from `web/skins/` (core) and from an optional extra directory `MXL_SKINS_DIR` (downstream, outside the repo).
- `GET /v1/skins` lists installed skins from their manifests; `/` serves the skin chosen by `MXL_SKIN` (default `classic`); `?skin=<id>` overrides per session (useful for operators and for previewing).
- A tiny shared client, `web/skins/_sdk/mxl-client.js` (no dependencies), provides: base URL discovery, token handling (the existing `#token=` convention), `state()`, `cut()`, …, an SSE subscriber with polling fallback, and error-code helpers. Skins should use it, and may skip it if they follow the same wire contract.

### 6.3 Rules every skin must follow

1. Call only `/api/mxl/v1/*` (and the program view URL from `/v1/meta`). No other backend paths, no direct control-port access.
2. No external hosts (fonts, scripts, analytics). CI scans every skin as it scans `local.html` today.
3. No secrets in the bundle; tokens come from the operator at runtime.
4. Declared capabilities must match actual use (a contract test greps the calls).
5. A **theme-only** tier is also allowed: a skin consisting of `skin.json` + `theme.css` (CSS variables, fonts, logo) that extends `classic`. Cheaper for branding changes than a whole new UI.

The in-repo Companion module (`companion-module-mxl-switcher/`) is the same kind of client as a skin and should move to `/v1` on the same schedule.

Server-side plugins (code running inside the control plane) are **out of scope for v1**; downstream bridges run as separate processes and talk to the same HTTP API.

## 7. Versioning, deprecation and downstream update flow

- **Two numbers:** *API version* (`v1`, in the path) and *core release* (`MAJOR.MINOR.PATCH`, git tag `core-vX.Y.Z`). The API major changes only on breaking changes.
- **Compatibility rules (v1):** adding fields, routes, events, capabilities = minor. Removing/renaming/retyping = new major (`v2`). Clients must ignore unknown fields.
- **Deprecation:** deprecated routes answer with `Deprecation` and `Sunset` headers, are listed in `CHANGELOG.md` and `/v1/meta`, and live for at least two minor releases or 90 days, whichever is longer. The current unversioned `/api/mxl/*` routes become aliases of `v1` for that window.
- **Release flow:** PR → CI (including contract tests, §8) → merge → tag `core-vX.Y.Z` with release notes (API changes called out first).
- **Downstream update flow:** a private downstream project depends on the core by **pinned release tag** (a git submodule or a vendored checkout at a tag). To take a fix: it is made and merged in core first, released, then the downstream bumps its pin in one small PR that runs the downstream's own tests against the new tag. Emergency patches carried downstream must have an open upstream PR and a removal date.
- **Pre-1.0 caveat:** until `core-v1.0.0` is tagged, the `v1` surface may still change; downstream should pin exact tags and read release notes.

## 8. Testing: contract tests

- **Schemas are the contract.** Check in `contracts/v1/` (OpenAPI 3.1 or JSON Schema per route/event) as the source of truth; generate the route table doc from it.
- **Core contract tests:** run the real routes against a mocked media-function server (the repo already has this pattern in `tests/js/`) and validate every response and SSE event against the schemas, including error codes and auth behaviour (401/429/503, token never logged).
- **Fixture-driven topology tests:** feed recorded `mxl-info -l` samples (the approach from #19) through manifest generation → `/v1/sources` → cut, so the contract holds for generated, non-lab UUIDs.
- **Skin conformance tests:** for each skin directory: manifest validates; required capabilities ⊆ core capabilities; static scan finds only `/api/mxl/v1/*` calls and no external hosts; declared capabilities cover observed calls; a headless load against a mock core renders without console errors.
- **Downstream reuse:** the same contract suite and mock core are published with each release so a downstream project can run them against its pinned tag and against its own skins/bridges.
- **Still needed regardless:** the scratch-VM smoke test (fresh quickstart → cut every source → program frame follows). Contract tests do not replace it.

## 9. Migration plan (suggested order)

1. Finish the open review PRs (#17 validation/timeouts, #19 generated manifest, #23 auth throttle; #20 self-healer after review) — the contract should be cut from the fixed code, not the current one.
2. Introduce `/api/mxl/v1/*` as thin wrappers over existing handlers + `/v1/meta`, stable source ids, and the error model; keep old routes as aliases. Add schemas and contract tests.
3. Add `/v1/events` (SSE) and source health (serve what `mxl_thumbs.py` already computes).
4. Skin loader, `skin.json`, SDK; move `local.html` → `skins/classic`; land #18 as `broadcast`.
5. Candidates, audio levels, layout presets (each a separate PR with its own tests); then turn the cockpit mock (#21) into a real skin.
6. Tag `core-v1.0.0`; downstream pins it.

## 10. Non-goals

Transitions beyond hard cut; multi-tenant auth; server-side plugin hosting; moving media-plane tools behind the API; any customer- or event-specific behaviour.

## 11. Open questions

**For the maintainer:**
1. Is a single shared token enough for v1, or do we want read vs control vs ops scopes from the start (§5.2)?
2. Candidates: should core persist them (file/SQLite) or keep memory-only with downstream re-posting on restart?
3. Should the v1 API remain HTTP-only, or do we also want a WebSocket for low-latency control from hardware panels (`companion-module-mxl-switcher/` polls today)?
4. Where do third-party skins live — only in `MXL_SKINS_DIR`, or also a `skins/` community folder in this repo?
5. Naming: keep the `/api/mxl/` prefix (`/api/mxl/v1/…`) or move to `/api/v1/`?

**For the other coder:**
6. Can `layout_pgm.py` / `audio_pgm.py` take their state from a small open endpoint in core (instead of a closed backend) without touching the lip-sync-critical paths?
7. Can `mxl_thumbs.py` health output be extended (stale vs wedged vs no-signal) and exposed cheaply, and can the audio tool emit levels at ~5 Hz without a new process?
8. What stable id should a source have when a guest flow is recreated (same person, new flow UUID)? The id must survive reconnects (relates to the stable-flow work in #22).
9. Does the self-healer (#20) conflict with `tools/guest_slot_watcher.py` when both rewire the selector?
10. Order of work: are you comfortable freezing feature work on `mxl-routes.js` until step 2 of §9 lands, to avoid merge conflicts across #17/#19/#20/#23?
