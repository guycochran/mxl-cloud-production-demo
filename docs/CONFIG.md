# Configuration reference (environment variables)

Site-specific endpoints used to be hard-coded across `scripts/`, `tools/` and `config/`.
They are now read from environment variables, **and every default is the current production
value** — so with nothing set, behaviour is identical to before. Set a variable only when
you are standing up a different site (or rotating an address).

A test (`tests/test_no_hardcoded_hosts.py`) fails if a *new* IP literal or site hostname is
added outside the allowlist, so new code should read config from the environment.

## Facility network (`config/facility.json` → `tools/facility.py`, `backend/facility.js`)

`facility.json` keeps its values as the defaults. These override them at load time (Python
and Node loaders both honour them):

| Variable | Overrides | Default (production) |
|---|---|---|
| `MXL_VM_IP` | `network.mxl_vm` — public/static IP the backend uses to reach the VM's easy-mxl APIs | `20.64.205.144` |
| `MXL_VM_INTERNAL_IP` | `network.mxl_vm_internal` — VNet address used VM-to-VM | `10.0.0.5` |
| `MXL_DOCKER_GATEWAY` | `network.docker_gateway` | `172.17.0.1` |
| `MXL_FACILITY_JSON` | path of the manifest file itself (pre-existing) | repo `config/facility.json` |
| `MXL_VM_URL` | full control-plane URL used by `backend/mxl-routes.js`, wins over the manifest (pre-existing) | `http://<mxl_vm>` |

## `scripts/bring-up-mxl.sh` (run from the backend box)

| Variable | Meaning | Default |
|---|---|---|
| `MXL_VM_IP` | VM public IP to SSH to | `20.64.205.144` |
| `MXL_VM_SSH_USER` | SSH user on the VM | `guy` |
| `MXL_SSH_KEY` | SSH private key | `$HOME/.ssh/mxl-lab` |
| `MXL_SITE_IP` | the one source IP the VM's NSG allows (only used in an error message) | `50.106.4.50` |
| `MXL_MAKITO_IP` | CAM 2 Makito X4 encoder (informational) | `192.168.8.177` |
| `MXL_BACKEND_URL` | facility backend base URL (kiosk page + `/api/mxl/*`); also exported to the VM-side `run-cam*.sh` as `MXL_REPAIR_URL=$MXL_BACKEND_URL/api/mxl/repair` | `https://prodbots.com` |
| `MXL_FEED_URL` | public WebRTC feed tunnel | `https://mxl-feed.cochran.cloud` |
| `MXL_AZ_RESOURCE_GROUP`, `MXL_AZ_VM_NAME` | `az vm start/deallocate` target | `ohg-mxl-lab`, `mxl-lab` |
| `MXL_HTML` | path of the deployed kiosk page | `$HOME/prodbots-backend/public/mxl.html` |

## Python / shell tools

| Variable | Used by | Default |
|---|---|---|
| `MXL_BACKEND_URL` | `audio_pgm.py`, `layout_pgm.py`, `mxl_multiview.py`, `nmos_node.py` (`--facility`), `selector-doctor.sh`, `guest-leg-doctor.sh` | `https://prodbots.com` |
| `TAMS_HOST` | `tams_shipper.py`, `backfill-mini.py` — the TAMS/MinIO box | `20.112.83.140` |
| `TAMS_URL` | `tams_shipper.py` TAMS API (pre-existing) | `http://$TAMS_HOST:8000` |
| `TAMS_S3_ENDPOINT` | `tams_shipper.py`, `backfill-mini.py` MinIO endpoint | `http://$TAMS_HOST:9000` |
| `TAMS_S3_USER` | MinIO access key id (the secret still comes from `~/.tams-s3.env`) | `tams` |
| `MXL_VM1_IP` | `guest-leg-doctor.sh`, `start-jonas-leg.sh`, `mv_encode.py` — fabric target / mediamtx host (VM1) | `10.0.0.4` |
| `MXL_VM2_IP` | `guest-leg-doctor.sh` — this box's fabric address (VM2) | `10.0.0.5` |
| `MXL_VM1_SSH_USER` | `guest-leg-doctor.sh` | `guy` |
| `MXL_MV_SRT_URL` | `mv_encode.py` full SRT publish URL (wins over `MXL_VM1_IP`) | `srt://$MXL_VM1_IP:8890?streamid=publish:multiview&latency=200` |

Already env-driven before this change (unchanged): `MXL_GUEST_HOST`, `MXL_GUEST_TRANSPORT`,
`MXL_REPAIR_URL`, `MXL_DOMAIN`, `MXL_BLEEDING_EDGE`, `MXL_GUEST_AUDIO`, `MXL_AUDIO_RTSP_HOST`.

## Security-related switches (see [SECURITY.md](../SECURITY.md))

| Variable | Used by | Default |
|---|---|---|
| `MXL_CONTROL_TOKEN` | `backend/mxl-routes.js` — require this shared token on `POST /api/mxl/input\|key\|pattern\|repair` | unset = open (startup warning) |
| `MXL_CONTROL_REQUIRE_TOKEN` | same — `1` = answer 503 if no token configured (fail closed) | unset |
| `MXL_REPAIR_RATE_MAX`, `MXL_REPAIR_RATE_WINDOW_S` | same — `/repair` calls per window per client (`0` = no limit) | `10`, `60` |
| `MXL_AUTH_FAIL_MAX`, `MXL_AUTH_FAIL_WINDOW_S` | `backend/mxl-auth.js` — failed-auth attempts per client before a `429` lockout (throttles brute-forcing the shared token; `0` = no limit) | `20`, `60` |
| `MXL_TRUST_PROXY_HEADERS` | `backend/mxl-auth.js` — use `CF-Connecting-IP` / `X-Forwarded-For` as the rate-limit client key (so visitors behind the tunnel get separate buckets, not the tunnel's one IP). Set `0` only if the server is exposed directly, not behind a trusted proxy | `1` (on) |
| `MXL_GUEST1_SRT_PASSPHRASE`, `MXL_GUEST2_SRT_PASSPHRASE`, `MXL_GUEST_SRT_PASSPHRASE` | `scripts/quickstart.sh` — require an SRT passphrase to publish on a guest slot | unset = open |
| `MXL_GRAPHICS_BIND` | `scripts/quickstart.sh` — bind address of the `:8085` graphics server | docker bridge gateway (e.g. `172.17.0.1`); `0.0.0.0` = old behaviour |

### Rolling out `MXL_CONTROL_TOKEN` safely

Several of our own components call the guarded routes (`/api/mxl/input`, `/api/mxl/repair`)
and therefore must present the token once the backend enforces it. Each of them sends
`X-MXL-Token: $MXL_CONTROL_TOKEN` **only if that variable is set in its own environment**
(no variable = same request as before): `contribution_core.py` (cam/guest ingest "announce"),
`layout_pgm.py`, `selector-doctor.sh`, `guest-leg-doctor.sh`, `bring-up-mxl.sh`, the kiosk page
(`/mxl.html#token=…`) and the Companion module (new *Control token* config field). Order:

1. Pick a token. Put `MXL_CONTROL_TOKEN` into the environment of every client above
   (systemd `Environment=`/`EnvironmentFile=`, the `run-cam*.sh` supervisors, the Companion
   config). Clients ignore it harmlessly while the backend is still open.
2. Verify, then set `MXL_CONTROL_TOKEN` on the **backend** and restart it. Watch its log for
   `rejected POST … (missing/invalid token)` lines — each one is a client you missed.
3. Rollback = unset the variable on the backend and restart (back to open behaviour).

## Not (yet) configurable

Intentionally left as literals — flagged for a follow-up, not changed here: the `/home/guy/...`
paths in `guest-leg-doctor.sh`, the static web pages' own hostnames (`web/*.html`:
`mxl-feed.cochran.cloud`, `mxlinfo.cochran.cloud`, canonical/og URLs), the exported Companion
page (`*.companionconfig`) and the Companion module's editable default host.
