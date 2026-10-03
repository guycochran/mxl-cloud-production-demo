# Contributing

This is an **open DMF/MXL interoperability lab**. The most valuable thing you can do
is **bring something and see if it works with MXL** — a source, a media function, a
cloud, a transport — and tell us where it broke. Those failures are the roadmap.

## Good ways to contribute

- **Run the quickstart on a fresh machine and report what happened.** Especially if it
  *didn't* go clone → on-air in a few minutes. Where you got stuck is a gap we want to
  close. See [docs/QUICKSTART.md](docs/QUICKSTART.md).
- **Add a source adapter.** The contribution seam
  ([docs/CONTRIBUTION-SEAM.md](docs/CONTRIBUTION-SEAM.md)) is designed so a new
  transport is one class in [`tools/adapters.py`](tools/adapters.py) and nothing in the
  core. SRT, RTSP, and native-MXL adapters already exist as examples.
- **Test interop with another MXL implementation.** If you have a second MXL
  source/function, point it at this lab and file what you find.
- **Field findings.** If you hit an MXL gotcha we haven't, add it to
  [docs/FINDINGS.md](docs/FINDINGS.md) — the numbered sections are outages we already
  had so the next person doesn't.

## Before you open a PR

```bash
pip install pytest
python -m pytest          # fast, no GStreamer/Docker needed (tests/ uses a stubbed gi)
```

CI runs the same suite plus `bash -n` on every shell script and a docs-link check
([.github/workflows/ci.yml](.github/workflows/ci.yml)). These tests exist to protect
the hard-won media core (the restamp/conform behaviour in
[`tools/contribution_core.py`](tools/contribution_core.py)) from well-meaning cleanup.
**If a test fails, re-read the cited FINDINGS section before changing the test** — the
constants and launch strings cost real debugging to discover.

## The one rule about the media core

Do **not** redesign the proven media core (conform / +2-grain restamp / mxlsink /
announce) unless adoption testing exposes a concrete reason. Front ends vary; the back
half is load-bearing and verified. Add adapters, not core rewrites.

## Scope

The switcher treats camera *control* (PTZ) as a replaceable, out-of-repo media function
(see the README). The video/contribution path, domain wiring, selector/keyer/audio
writers, multiview, and TAMS clipper are all here and buildable.

## Questions

Open a [Discussion](https://github.com/guycochran/mxl-cloud-production-demo/discussions)
for "how would I…" / "does MXL do…", and an Issue for a concrete bug or gap.
