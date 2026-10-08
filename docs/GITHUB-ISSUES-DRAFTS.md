<!-- SPDX-License-Identifier: Apache-2.0 -->
# Starter GitHub issues — drafts

Deliberately approachable entry points so a prospective contributor has somewhere to
start without asking what to work on. Labels in brackets are suggestions. These can be
created with `scripts/create-starter-issues.sh` (dry-run by default) or by hand.

---

## 1. Add CI tests for SourceAdapter / ContributionCore  [good first issue]

**Done — tracking for visibility.** `tests/` now has stubbed-gi unit tests + a golden
launch-string parity test, run in `.github/workflows/ci.yml`. Good follow-on first
issues: add a test for `cam2`/Makito launch strings, or for `guest_slot_watcher`'s
`flow_map()` parser against sample `mxl-info` output.

Files: `tests/`, `tools/contribution_core.py`, `tools/adapters.py`

---

## 2. Pin known-good CBC/MXL container versions  [easy]

**Done — tracking for visibility.** Images are pinned by digest in
`scripts/quickstart.sh` + `docker/guest-ingest.Dockerfile`, documented in
`docs/VERSIONS.md`, with `MXL_BLEEDING_EDGE=1` opt-in. Follow-on: add a small CI job
(or `make refresh-pins`) that resolves current `:latest` digests and diffs them against
the pins so we notice upstream drift.

Files: `scripts/quickstart.sh`, `docker/guest-ingest.Dockerfile`, `docs/VERSIONS.md`

---

## 3. `scripts/doctor.sh` — health check  [easy–medium]

**Shipped — follow-ons open.** `scripts/doctor.sh` reports container APIs, per-flow
unique-fps liveness, and the program path. Good next steps: (a) surface it in the
quickstart banner; (b) add a `--json` mode for machine consumption; (c) add a
`--wait-until-healthy` for CI/smoke use.

Files: `scripts/doctor.sh`

---

## 4. Add an RTSP camera option to the quickstart  [medium]

The quickstart gives you pattern + clip + two SRT guest slots. Add an **RTSP camera**
on-ramp: a flag (or the planned `setup.sh` menu) that wires `RtspCamAdapter` to a
selector slot given an `rtsp://…` URL. The adapter already exists
(`tools/adapters.py`); the work is the quickstart plumbing + a slot + docs.

Files: `scripts/quickstart.sh`, `tools/adapters.py`, `docs/QUICKSTART.md`

---

## 5. Wire guest audio into the quickstart  [medium–hard]

Guest **video** is a first-class MXL source in the quickstart; **audio** is not yet.
Generalise the contribution path so a guest's audio flow is created, conformed, and
available to program with reliable audio-follow-video. `tools/guest_audio.py` and
`tools/audio_pgm.py` are the starting points. This is the hard line between demo and
production — see the ZoomISO runbook's audio unknowns.

Files: `tools/guest_audio.py`, `tools/audio_pgm.py`, `tools/contribution_core.py`, `scripts/quickstart.sh`

---

## 6. Harden guest containers for Contribution Tier 2  [medium]

`docs/CONTRIBUTION-SPLIT.md` describes three isolation tiers. Tier 2 (single-box,
container-hardened) is specced but not wired: add `--memory` / `--cpus` / dropped
capabilities / read-only rootfs to the guest-ingest containers so an untrusted
contributor's stream can't exhaust the host. Measure the overhead.

Files: `scripts/quickstart.sh`, `docker/guest-ingest.Dockerfile`, `docs/CONTRIBUTION-SPLIT.md`

---

## 7. Test a native MXL source from a second implementation  [interop]

The `needs_conform=False` / native-MXL path is proven only against our own synthetic
source (`tools/zoomiso_dryrun.py`). If you have **another MXL implementation** that
writes a v210 flow, point it at this lab and report: does it drop straight into a
selector slot? Is it domain-clock-aligned or does it need the restamp? This is exactly
the interop feedback the lab exists to produce.

Files: `tools/adapters.py` (`ZoomIsoMxlAdapter` as the template), `docs/CONTRIBUTION-SEAM.md`

---

## 8. ZoomISO Cloud beta interoperability  [blocked: external]

**Blocked on access to the ZoomISO Cloud beta (liminalet.com/zoomiso-cloud).** All the
plumbing is built and dry-run-verified (`ZoomIsoMxlAdapter`, `zoomiso_adapters()` for
both flow shapes, `tools/zoomiso_dryrun.py`, `docs/ZOOMISO-BETA-RUNBOOK.md`). When
access lands, the first-30-minutes runbook measures the three real unknowns (flow shape,
audio, clock) and wires it. Comment here if you have ZoomISO Cloud access and want to
help test.

Files: `tools/adapters.py`, `tools/zoomiso_dryrun.py`, `docs/ZOOMISO-BETA-RUNBOOK.md`
