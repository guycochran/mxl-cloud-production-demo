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
