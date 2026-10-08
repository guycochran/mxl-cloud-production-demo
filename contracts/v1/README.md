<!-- SPDX-License-Identifier: Apache-2.0 -->
# `contracts/v1/` — the MXL switcher v1 API contract (SKETCH)

JSON Schemas are the **source of truth** for the `/api/mxl/v1` surface. The design
rationale, route table, auth scopes, and error-code → HTTP mapping are in
[`docs/V1-CONTRACT.md`](../../docs/V1-CONTRACT.md). This is a **draft sketch** — the
handlers aren't wired yet; the schemas exist so we can agree on the shape first.

| File | Covers |
|---|---|
| `meta.schema.json` | `GET /v1/meta` — version + capability + auth discovery |
| `state.schema.json` | `GET /v1/state` and the body returned by every mutating route |
| `sources.schema.json` | `GET /v1/sources` — switchable sources with **stable ids** |
| `requests.schema.json` | request bodies for `/v1/cut`, `/preview`, `/take`, `/key` |
| `error.schema.json` | the one error body every route returns on failure |

## Example payloads

`GET /v1/meta`
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

`GET /v1/sources`
```json
{
  "sources": [
    { "id": "src_9998da48", "label": "Playout",  "kind": "playout",
      "order": 0, "health": { "state": "ok", "last_frame_age_ms": 34 } },
    { "id": "src_d3e15194", "label": "Pattern",  "kind": "generator",
      "order": 1, "health": { "state": "ok", "last_frame_age_ms": 33 } }
  ]
}
```

`GET /v1/state`  (also the response to a successful `cut`/`preview`/`take`/`key`)
```json
{ "pgm": "src_d3e15194", "pvw": null, "key": true, "busy": false }
```

`POST /v1/cut`
```json
{ "source": "src_9998da48" }
```

error (e.g. cutting to a source no longer wired into the selector)
```json
{ "error": { "code": "source_not_attached",
             "message": "source \"Guest 1\" is not attached to the selector" } }
```

## Why these and not the current routes

The live routes are `/api/mxl/*` (unversioned), address sources by layout **index** or
**role name**, overload `live` to mean "wired," and return `{error:"<string>"}` with
inconsistent status codes. A skin written against that breaks on the next box (a fresh
quickstart generates different flow UUIDs — review R2). `v1` fixes exactly those: a
**stable opaque id** per source, a real **health** object, a structured **error model**,
and **capability discovery** so a skin knows what a core offers before it renders.

These are thin wrappers — the media behaviour underneath is unchanged. See
`docs/V1-CONTRACT.md` for the full mapping and the (deferred) implementation plan.
