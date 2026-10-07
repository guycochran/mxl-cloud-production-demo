# Pinned container versions

An adoptable reference implementation must be **reproducible**: a tutorial that
worked last month should work next month. Floating `:latest` tags make that
impossible — an upstream image rebuild can break a quickstart for reasons that have
nothing to do with this repo's code.

So `scripts/quickstart.sh` and `docker/guest-ingest.Dockerfile` pull images **by
digest** by default. These are the exact images the Oct 2026 cold-clone proofs ran
against (GCP 2m33s / AWS 3m59s / Azure 4m17s → ON AIR → cut HTTP 200).

## Known-good digest matrix (Oct 2026)

| Image | Registry | Digest |
|---|---|---|
| `bluenviron/mediamtx` | Docker Hub | `sha256:5ce2a948eb68df06ce2e13870db8df8e30d516ac4dc40e04bfe8aee3bdf7be40` |
| `ghcr.io/cbcrc/test-generator` | GHCR | `sha256:09cad0981475095ab948ca51511d4fbdc0521e2a23632d50abaf14fc3847cd92` |
| `ghcr.io/cbcrc/file-player` | GHCR | `sha256:149953ce851a6d5e2ffbc9bd39c7e82ae529af25c20237457a77033c24e8f297` |
| `ghcr.io/cbcrc/input-selector` | GHCR | `sha256:c1e869ea39985ae195951a1d3d78e08001e4521017acad0632a79d48b936e3d1` |
| `ghcr.io/cbcrc/html5-keyer` | GHCR | `sha256:419ac23e1d75ad0c94b437dd709cc0d701b0dacfe3679733b5658a530bc86660` |
| `ghcr.io/cbcrc/mxl2webrtc` | GHCR | `sha256:ca047e75714bfad97239060f35d7e96e37decc6dacdf747c539940cc59fd37f6` |

These values live in one place in the code: the `PIN_*` map at the top of
`scripts/quickstart.sh`. `docker/guest-ingest.Dockerfile` pins its base
(`test-generator`) via a build arg that defaults to the same digest.

## MXL SDK version (inside the base image)

The MXL SDK (`libmxl.so` + the `mxlsink`/`mxlsrc` GStreamer plugins) is **not
built by this repo** — it ships *inside* the pinned `ghcr.io/cbcrc/test-generator`
base digest above, which `docker/guest-ingest.Dockerfile` builds `FROM`. So the
effective SDK version is whatever that digest baked in:

| Component | Pinned to | Expected version |
|---|---|---|
| MXL SDK (Flow + Fabric API) | the `test-generator` base digest `sha256:09cad09…` | **v1.1.0** (released 2026-09-09; Fabric API) |
| GStreamer | — | 1.24.x |

Why this matters: a future base-digest refresh could silently change the SDK
version. The Flow API `mxlsink` behaviour we depend on — one shared clock offset
`D` sampled per pipeline, `index = timestamp_to_index(pts + base_time + D)`, and
**no writer-side backward-index guard** (the writer requires the caller to present
monotonic PTS; that is exactly what `contribution_core._restamp`'s monotonic clamp
provides) — is the v1.1.0 `gst-mxl-rs` contract. Pin and verify it.

**Verify the SDK version in the pinned base before a cold-clone proof:**

```bash
IMG=ghcr.io/cbcrc/test-generator@sha256:09cad0981475095ab948ca51511d4fbdc0521e2a23632d50abaf14fc3847cd92
# plugins present + their version
docker run --rm --entrypoint sh "$IMG" -c 'gst-inspect-1.0 mxlsink | grep -iE "Version|Filename"; gst-inspect-1.0 mxlsrc >/dev/null && echo mxlsrc=OK'
# libmxl SONAME / version strings
docker run --rm --entrypoint sh "$IMG" -c 'f=$(find / -name "libmxl*.so*" 2>/dev/null | head -1); echo "$f"; strings "$f" 2>/dev/null | grep -iE "^1\.[01]\.[0-9]+$|v1\.[01]" | head'
```

If that reports anything other than v1.1.0, do **not** treat a green run as
v1.1.0-conformant — update this table and re-run the HW proof. (The standalone
fabric build on the GCP/Azure boxes already pins the SDK explicitly:
`git clone dmf-mxl/mxl && git checkout v1.1.0` — see
`docs/JONAS-FABRIC-HANDOFF.md`.)

## Bleeding-edge mode (opt-in)

To test against the current upstream `:latest` instead of the pinned digests —
e.g. to validate a new cbcrc release before updating the pins:

```bash
sudo MXL_BLEEDING_EDGE=1 scripts/quickstart.sh
```

If that run is healthy (`scripts/doctor.sh` all green, a real cut lands), update the
digests above and the `PIN_*` map, and note the date.

## Refreshing the pins

```bash
# resolve the current :latest digest for a ghcr image without pulling it
repo=cbcrc/input-selector
tok=$(curl -s "https://ghcr.io/token?scope=repository:$repo:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
curl -sI -H "Authorization: Bearer $tok" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  "https://ghcr.io/v2/$repo/manifests/latest" | grep -i docker-content-digest
```
