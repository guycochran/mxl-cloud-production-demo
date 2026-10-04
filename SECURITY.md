# Security Policy

This is a **reference/lab project**, not a hardened production service. It is meant to
be stood up on a throwaway VM, driven, and torn down. Please treat it accordingly.

## Reporting a vulnerability

If you find a security issue, please **do not open a public issue**. Report it
privately via GitHub's [private vulnerability reporting](https://github.com/guycochran/mxl-cloud-production-demo/security/advisories/new)
(Security tab → "Report a vulnerability"). We'll acknowledge and respond as fast as we
reasonably can.

## Things to know before you deploy this

The quickstart and demo prioritise *being easy to run and inspect* over being locked
down. In particular:

- **The quickstart binds control APIs on `127.0.0.1`** (selector `:9604`, keyer `:9605`,
  etc.) and opens WebRTC + SRT ports to the world so you can watch/contribute. The
  control APIs are unauthenticated — **do not expose them to the public internet.** Keep
  them behind `localhost`, a firewall, or a tunnel with access control.
- **Backend control routes** (`backend/mxl-routes.js`: `POST /api/mxl/input|key|pattern|repair`)
  can cut the live program and rebuild the cascade. They are open by default for
  backwards compatibility, and the backend logs a startup warning saying so. To lock them
  down, set **`MXL_CONTROL_TOKEN`** in the backend's environment: the four POST routes
  then require `Authorization: Bearer <token>` or `X-MXL-Token: <token>` (`GET /status`
  stays open). The kiosk page takes the token once via `/mxl.html#token=<secret>` (kept in
  that browser's localStorage). Set `MXL_CONTROL_REQUIRE_TOKEN=1` to fail closed (503) if
  the token is ever missing. `/repair` is also rate limited (default 6 per 60 s per client;
  tune with `MXL_REPAIR_RATE_MAX` / `MXL_REPAIR_RATE_WINDOW_S`, `0` disables). A shared
  token is a speed bump, not a substitute for network controls — still keep the VM
  control ports behind a firewall/NSG. Always serve the backend over HTTPS when a token is in use.
- **The SRT guest slots accept any publisher** that knows the stream id — that's the
  point (open contribution), but it means anyone who learns `srt://your-ip:8890` +
  `publish:guest1` can push video. Rotate/scope stream ids for anything beyond a demo.
- **Container images are pinned by digest** ([docs/VERSIONS.md](docs/VERSIONS.md)) for
  reproducibility; `MXL_BLEEDING_EDGE=1` opts into upstream `:latest`.
- **No secrets belong in this repo.** The open core is deliberately decoupled from the
  live deployment's backend/tunnel/tokens; the quickstart needs none of them.

## Supported versions

This project tracks `master`. There are no long-lived release branches to backport to;
fixes land on `master`.
