<!-- SPDX-License-Identifier: Apache-2.0 -->
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

Open a [Discussion](https://github.com/guycochran/mxl-switcher/discussions)
for "how would I…" / "does MXL do…", and an Issue for a concrete bug or gap.

## License of contributions

This project is licensed under the [Apache License 2.0](LICENSE). By contributing, you
agree that your contribution is licensed under Apache-2.0 (Apache-2.0 §5). There is **no
CLA** — instead every commit carries a Developer Certificate of Origin sign-off.

## Developer Certificate of Origin (DCO)

Every commit in a pull request must be **signed off**, certifying the DCO 1.1 below. Add
the sign-off with `-s`:

```bash
git commit -s -m "fix: describe the change"
```

That appends a trailer using your `git config user.name` / `user.email`:

```
Signed-off-by: Your Name <you@example.com>
```

The sign-off email must match the commit's author email. CI runs a DCO check on every PR
(`scripts/check-dco.sh`, workflow `.github/workflows/dco.yml`). Forgot it? Fix the
branch and force-push it:

```bash
git commit --amend -s --no-edit          # last commit only
git rebase --signoff origin/master       # every commit on the branch
git push --force-with-lease
```

Commits written with an AI assistant are fine; the human who submits them signs off and
takes responsibility for them under the DCO, the same as for any other commit.

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
