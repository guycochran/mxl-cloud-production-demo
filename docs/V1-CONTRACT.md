# The `/api/mxl/v1` contract (minimal first slice — SKETCH)

**Status:** DRAFT sketch for discussion. Schemas live in `contracts/v1/`. No handlers
are rewritten yet — this proposes the *shape* so we can agree on it before wiring.
Follows the architecture in `docs/ARCHITECTURE-CORE-AND-SKINS.md` (#24) and the review
reply on that PR: ship a **minimal** contract for `core-v1.0.0`, add the rich data
APIs (candidates, audio, layouts, SSE) as additive `v1.1+`.

## Why a versioned contract at all

The open switcher is meant to be the **bones** that skins and downstream projects
build on (a broadcast skin, a broadcast panel, a director cockpit). Today the only contract
is the unversioned `/api/mxl/*` routes, whose shapes leak internals and whose identity
model (slot index + role name) is fragile — a fresh quickstart box generates *different*
flow UUIDs, and the layout roles are inconsistent (review R2/R3). A skin written against
that breaks on the next box. `v1` is the stable, discoverable surface a skin targets
once, against any core instance.

## Scope of this slice (what `core-v1.0.0` commits to)

Read + control + discovery, as **thin wrappers over the existing handlers** in
`backend/mxl-routes.js` + `backend/local-server.js`. No new media behaviour.

| v1 route | Method | Auth scope | Wraps today | Schema |
|---|---|---|---|---|
| `/api/mxl/v1/meta` | GET | open | *(new, tiny)* | `meta.schema.json` |
| `/api/mxl/v1/state` | GET | read | `GET /api/mxl/status` | `state.schema.json` |
| `/api/mxl/v1/sources` | GET | read | `GET /api/mxl/status` + `/slots` merged | `sources.schema.json` |
| `/api/mxl/v1/thumbs/:id.jpg` | GET | read | `GET /api/mxl/thumbs/:name` | *(image)* |
| `/api/mxl/v1/cut` | POST | control | `POST /api/mxl/input` | `cut.request.schema.json` → `state` |
| `/api/mxl/v1/preview` | POST | control | `POST /api/mxl/preview` | `preview.request.schema.json` → `state` |
| `/api/mxl/v1/take` | POST | control | `POST /api/mxl/take` | → `state` |
| `/api/mxl/v1/key` | POST | control | `POST /api/mxl/key` | `key.request.schema.json` → `state` |

Every error on every route uses the one **error model** (`error.schema.json`).

**Explicitly NOT in v1 (operational or additive):**
- `/api/mxl/warmup`, `/api/mxl/repair` → stay as **unversioned `/ops/*`** (operational,
  not contract). A skin may call them only via an `ops.*` capability, never by default.
- `/api/mxl/pattern` → folds under an optional `test-source` capability (post-v1.0).
- candidates, audio levels, layout presets, SSE events → **v1.1+**, each additive.
- `/program`, `/mxl2webrtc/*` → **media plane**, not the JSON contract; its URL is
  advertised in `/v1/meta.program_url` and a skin embeds it as an opaque view.

## The one real new concept: stable source ids

Today a source is addressed by **layout slot index** (`0,1,2…`) or **role name**
(`cam`, `pattern`). Both are unstable: indices shift as guests attach; roles are
inconsistent across manifests; and the underlying flow UUID differs per box (R2).

`v1` gives every source an **opaque stable `id`** plus a human `label` and a `kind`.
The id is **derived deterministically from the flow UUID** (so it's stable for the life
of that flow and identical between `/state` and `/sources`), but opaque to the client —
a skin stores the id and never parses it.

```
id     = "src_" + first 8 hex of the source's flow UUID   (e.g. "src_9998da48")
label  = human label from the manifest                    (e.g. "Playout")
kind   = camera | guest | playout | generator | layout | other
```

- **Cut by id, not index:** `POST /v1/cut {"source":"src_9998da48"}`. Indices become
  display order only (`order` field), never an address.
- **Resolution is internal:** the wrapper maps `id → flow UUID → the layout slot /
  selector index` the existing `toLayoutSlot` + `mxlSetInput` already handle. The strict
  input validation from #17 stays underneath; v1 just narrows the accepted address to
  the id form.
- **`health` replaces the overloaded `live`.** Today `live` means "wired into the
  selector," not "has fresh video." v1 reports `health: { state, last_frame_age_ms }`
  with `state ∈ {ok, no_signal, stale, wedged, unknown}`. For v1.0 this can be derived
  from what we already know (wired + thumb freshness from `mxl_thumbs.py`'s health.json);
  richer detection is additive.

## Auth scopes (decided: do them from the start)

Three scopes, mapped over the gating that already exists in `backend/mxl-auth.js`:

| scope | covers | default |
|---|---|---|
| `read` | `GET /v1/state`, `/v1/sources`, `/v1/meta`, `/v1/thumbs` | **open** (set `MXL_REQUIRE_READ_AUTH=1` to require a token) |
| `control` | `POST /v1/cut`, `/preview`, `/take`, `/key` | token when `MXL_CONTROL_TOKEN` set (current behaviour) |
| `ops` | `/ops/warmup`, `/ops/repair` | token; a skin needs the `ops.*` capability to surface them |

`/v1/meta.auth` advertises `{ required: bool, scopes: [...] }` so a skin's loader can
tell what the running core enforces before it renders. One shared token still works
(it simply satisfies all scopes); per-scope / per-operator tokens are future work.

## Error model (one shape everywhere)

```json
{ "error": { "code": "source_not_attached", "message": "source \"Guest 1\" is not attached to the selector" } }
```

Stable `code` values (clients branch on `code`, show `message`):

| code | HTTP | meaning (maps from today) |
|---|---|---|
| `unknown_source` | 400 | id not in the layout (`toLayoutSlot` → -1) |
| `source_not_attached` | 409 | valid id, not wired into the selector right now |
| `busy` | 409 | a switch is in progress — **retryable** |
| `selector_down` | 502 | the media function didn't answer |
| `timeout` | 504 | upstream MXL API timed out (the #17 AbortSignal path) |
| `rate_limited` | 429 | throttled (#23); `retry_after_s` included |
| `unauthorized` | 401 | missing/invalid token for the route's scope |
| `not_configured` | 503 | `MXL_CONTROL_REQUIRE_TOKEN=1` but no token set |

## `/v1/meta` — version + capability discovery

The one genuinely new endpoint. A skin reads it first to decide if it can run:

```json
{
  "api_version": "1.0",
  "core_version": "0.3.0",
  "capabilities": ["state.read","sources.read","thumbs.read",
                   "control.cut","control.preview","control.take","control.key"],
  "program_url": "/program",
  "auth": { "required": false, "scopes": ["read","control","ops"] }
}
```

A skin declares the capabilities it needs (`skin.json`, see #24 §6); the loader loads it
only if `needs ⊆ meta.capabilities`, otherwise shows a clear "this core lacks X" page.
Optional capabilities degrade gracefully. This is what lets the same `broadcast` skin run
against a stripped-down core and a full one without breaking.

## Compatibility rules (v1)

- Additive = minor (new routes/fields/events/capabilities). Clients MUST ignore unknown
  fields.
- Removing/renaming/retyping = new major (`v2`) with an overlap window.
- The current unversioned `/api/mxl/*` routes become **aliases of `v1`** for the overlap
  window (so `web/local.html` keeps working while it's ported to the SDK client).

## How this gets implemented (not in this PR)

1. Merge the open review PRs first (#17/#19/#20/#22/#23) — cut the contract from the
   *fixed* code, per the architecture doc §9.1.
2. Add `backend/mxl-routes-v1.js`: thin handlers that call the existing `mxlStatus` /
   `mxlSetInput` / etc., translate to the id model + error model, and register both
   `/v1/*` and the legacy aliases.
3. Add `contracts/v1/*` (these schemas) as the source of truth; a contract test
   validates every response against them, reusing the `tests/js/` mock-MXL pattern and
   #19's recorded `mxl-info -l` fixtures so it holds for generated, non-lab UUIDs.
4. Tag `core-v1.0.0`. Downstream (incl. a private downstream project)
   pins it.

## Open items for the maintainer / reviewer

- `core_version` source: the `package.json` version, or a dedicated `core-vX.Y.Z` git
  tag? (Architecture doc proposes the tag.)
- Is `src_` + 8 hex enough id entropy for a single box? (Collisions need two flows whose
  UUIDs share the first 32 bits — vanishingly unlikely in one domain; we can widen to 12
  hex if ever needed, additively.)
- Confirm `health.state` derivation for v1.0: wired + thumb-age only, or wait for the
  `mxl_thumbs.py` health.json to be served (it's computed but not yet exposed).
