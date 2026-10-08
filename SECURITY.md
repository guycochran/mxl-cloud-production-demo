# Security Policy

This is a **reference/lab project**, not a hardened production service. It is meant to
be stood up on a throwaway VM, driven, and torn down. Please treat it accordingly.

## Reporting a vulnerability

If you find a security issue, please **do not open a public issue**. Report it
privately via GitHub's [private vulnerability reporting](https://github.com/guycochran/mxl-switcher/security/advisories/new)
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
  the token is ever missing. `/repair` is also rate limited (default 10 per 60 s per client;
  tune with `MXL_REPAIR_RATE_MAX` / `MXL_REPAIR_RATE_WINDOW_S`, `0` disables). A shared
  **Behind a reverse proxy / tunnel, set `MXL_TRUST_PROXY=1`** so limits key on the real
  client (`CF-Connecting-IP` / `X-Forwarded-For`); it is OFF by default because on a
  directly exposed server those headers are client-controlled. A shared
  token is a speed bump, not a substitute for network controls — still keep the VM
  control ports behind a firewall/NSG. Always serve the backend over HTTPS when a token is in use.
- **The SRT guest slots accept any publisher** — that's the point (open contribution).
  In the default `srt-listen` transport each guest owns a UDP port (`8890` = Guest 1,
  `8891` = Guest 2) and anyone who can reach that port can push video; the passphrase
  options below are **not applied** in this mode, so restrict UDP 8890/8891 to known
  source IPs in your cloud firewall for anything beyond a demo.
  **Optional hardening (off by default, `MXL_GUEST_TRANSPORT=srt-direct` only):** run the
  quickstart with a per-guest SRT passphrase and mediamtx will reject any publisher on that slot that doesn't encrypt with it:
  ```bash
  sudo MXL_GUEST1_SRT_PASSPHRASE='long-secret-for-guest-1' \
       MXL_GUEST2_SRT_PASSPHRASE='different-secret-guest-2' scripts/quickstart.sh
  # or one shared secret for both slots:  MXL_GUEST_SRT_PASSPHRASE='…'
  ```
  Passphrases are 10–79 characters (SRT's limit; no quotes/backslashes). The guest then
  enters the same value in Larix's **Passphrase** field (or `…&passphrase=SECRET` on an
  ffmpeg/OBS SRT URL). The config is written to `/srv/mxl-quickstart/mediamtx.guest-auth.yml`
  (mode 600) and mounted into mediamtx; it only affects the guest slots you set a passphrase
  for. With nothing set, behaviour is unchanged and the quickstart prints a reminder.
  Rotating = re-run the quickstart with a new value (briefly restarts mediamtx).
- **The graphics server (`:8085`)** is bound to the docker bridge gateway (reachable by the
  keyer container, not by the internet) rather than all interfaces, and serves files by exact
  name only (no directory listing). `MXL_GRAPHICS_BIND=0.0.0.0` restores the old behaviour.
- **Browser pages that load CDN scripts** (`web/mxl-clip.html`, `web/mxl-tams.html`) pin
  hls.js to 1.5.15 and qrcodejs to an exact commit, both with Subresource Integrity hashes.
- **Site configuration** (VM IPs, backend/TAMS hosts, ...) is read from environment variables with
  production defaults — see [docs/CONFIG.md](docs/CONFIG.md).
- **Container images are pinned by digest** ([docs/VERSIONS.md](docs/VERSIONS.md)) for
  reproducibility; `MXL_BLEEDING_EDGE=1` opts into upstream `:latest`.
- **No secrets belong in this repo.** The open core is deliberately decoupled from the
  live deployment's backend/tunnel/tokens; the quickstart needs none of them.

## Supported versions

This project tracks `master`. There are no long-lived release branches to backport to;
fixes land on `master`.
