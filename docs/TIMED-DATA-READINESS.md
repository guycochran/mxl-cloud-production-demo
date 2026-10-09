<!-- SPDX-FileCopyrightText: 2026 Contributors to the Media eXchange Layer project. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Timed Data (MXL v1.2 event flows) — switcher readiness note

**Status:** tracking + design sketch. No code yet; the upstream feature is not merged.
This is Tier 3.2 of `docs/ROADMAP-FROM-SPECS-2026-10.md` — get ahead of the v1.2
"Timed Data" feature so we are the reference consumer the day it lands.

## What's coming upstream

AMWA **IN-... / dmf-mxl #327** "Exchange of Timed Data through MXL" adds a new flow
format for **non-periodic, strongly-timestamped, variable-size, self-describing** grains —
the shared-memory analog of SMPTE **ST 2110-41** (FMX). Implementation is **PR #720**
(`dmf-mxl/mxl`, review-complete, near merge as of Oct 2026), with two carved-out child
tasks: **#731** (mxl-fabrics transport) and **#730** (Rust bindings).

### The v1.2 API surface (from PR #720, for when we build against it)

New flow format `MXL_DATA_FORMAT_EVENT` (`urn:x-nmos:format:event`). A **double-ended
queue where the index does NOT imply a timestamp** — the timestamp lives in the event
struct, not in the ring position (the key departure from video/audio flows).

- Writer: `mxlFlowWriterOpenEvent` → edit `mxlEventInfo` + payload → `mxlFlowWriterCommitEvent`
  (or `...CancelEvent`). One event open at a time.
- Reader: `mxlFlowReaderGetEvent(timeoutNs, …)` / `...GetEventNonBlocking`. One producer,
  N independent readers (each its own cursor/instance).
- `mxlEventInfo` (512 B, v1): `timestamp` (TAI ns, **must be non-decreasing**),
  `registryType` + `dataItemType[256]` (SMPTE ST 2110-41 Admin Register, or the DMF MXL
  URN scheme `x-mxl:…` — the MXL scheme is still TODO upstream), `eventSize`/`offset`/`complete`
  (manual fragmentation; interleaving logical events is forbidden), `index` (read-assigned).
- Capacity from grain_rate × history (default 200 ms), clamp 2..65536.
- Ships `mxl-data-probe` + ST 2110-41 ADM/DIT test pcaps.

## How it maps onto this switcher

We already have the **v1.1 path** for the same payloads: the `data` essence shipped in
Tier 3.1 (`CaptionFileAdapter` → `meta/x-st-2038 ↔ video/smpte291` data flow via
`mxlsink`). That carries CEA-608/708 captions, and could carry SCTE-104/35, tally, ADM
metadata — frame-aligned, inside an ST-2038 wrapper.

Event flows are the **v1.2 path** for the *same information* but modelled natively:
aperiodic, per-event timestamped, type-registered — no ST-2038 framing needed, and
(via #731) fabric-portable to a second host. The two are complementary:

| | v1.1 data flow (shipped, Tier 3.1) | v1.2 event flow (#327/#720, pending) |
|---|---|---|
| MXL format | `video/smpte291` (ST-2038 ANC) | `MXL_DATA_FORMAT_EVENT` |
| Cadence | frame-aligned (periodic grid) | aperiodic, event-timestamped |
| Typing | ANC DID/SDID inside 2038 | DIT + external registry (SMPTE / MXL URN) |
| GStreamer | `cctost2038anc`/`st2038anctocc` exist today | no gst element yet (C API first) |
| Our seam | `essence='data'` adapter + core branch | a new `essence='event'` sidecar (below) |

## Design sketch: a guest event-flow sidecar

When #720 lands, the smallest useful consumer is a **sidecar alongside a guest's A/V**
that publishes/reads an event flow carrying that guest's timed data (captions from an ASR
feed, SCTE triggers, tally). It reuses the seam's shape:

- A new `essence='event'` in `contribution_core` — but event flows do **not** go through
  GStreamer `mxlsink` (no gst element yet), so this is NOT a launch-string branch. Instead a
  small Python loop using the v1.2 **Rust/C bindings** (#730): open the event writer, and on
  each incoming datum set `timestamp` (our local TAI, same clock the restamp locks),
  `registryType`/`dataItemType`, copy the payload, commit.
- Timestamp discipline is the natural fit for our existing model: events are stamped on the
  **same local clock** the A/V restamp locks to, so a caption event and its video grain
  share one time base — exactly the lip-sync invariant the A/V seam already maintains.
- The IN-005 ingress registry (Tier 2.1) extends cleanly: an event-flow ingest writes the
  same `/tmp/mxl-ingress/<flow8>.json` record (provenance = registry/DIT, timing = the
  per-event timestamps), so `ingress-soak.sh` can watch it too.

## Blocking dependencies before we build

1. **#720 merged** + a tagged release that includes it (we pin the SDK by base-image
   digest — see `docs/VERSIONS.md`; the caption/event build is gated behind an opt-in arg).
2. **#730 (Rust bindings)** — our tools are Python-over-gst or Python-over-bindings; the
   event API is C/Rust first. Until bindings exist, a prototype would be a small C or Rust
   helper, not a gst pipeline.
3. The **MXL URN registry scheme** (TODO in #720) if we want DMF-native typing rather than
   borrowing the SMPTE 2110-41 register.

## Next actions (tracking only)

- [ ] Watch dmf-mxl **#720 / #731 / #730** for merge; re-read the final API before building.
- [ ] When bindings land, prototype the `essence='event'` sidecar above against a guest.
- [ ] Decide registry: SMPTE 2110-41 Admin Register vs. the DMF MXL URN scheme.

Ties: `docs/ROADMAP-FROM-SPECS-2026-10.md` (Tier 3.1 data flow = the v1.1 sibling),
`docs/CONTRIBUTION-SEAM.md` (the adapter/essence model this extends).
