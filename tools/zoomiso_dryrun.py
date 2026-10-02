#!/usr/bin/env python3
"""zoomiso_dryrun — prove the ZoomISO Cloud pipeline WITHOUT the real product.

ZoomISO Cloud (when we get the beta) emits native MXL v210 grains. We don't have it
yet, but we can prove our ingest end-to-end TODAY by standing in a *synthetic* native
v210 flow and running the real ZoomIsoMxlAdapter against it. If this harness turns a
fake "ZoomISO" flow into a cuttable selector slot, then on beta day the only unknowns
left are the ones in docs/ZOOMISO-BETA-RUNBOOK.md (flow shape, clock) — the plumbing
is already proven.

This is a HARNESS, not production: it uses ContributionCore + ZoomIsoMxlAdapter exactly
as the real path will, but the "ZoomISO source" is faked by a second mxl flow already
present in the domain (e.g. the quickstart's Pattern/Clip flow, or a test-generator you
point it at). That exercises the native-MXL branch: needs_conform=False, mxlsrc, the
timing_policy decision — everything except the real Zoom bytes.

Usage (inside a container that has gst+mxl, e.g. mxl-guest-ingest:local):
  zoomiso_dryrun.py <fake-source-flow-uuid> <target-slot-flow-uuid> [align|restamp]

The <fake-source-flow-uuid> is any EXISTING v210 flow in the domain (stands in for a
ZoomISO participant flow). The adapter reads it via mxlsrc and republishes to the slot,
proving the ZoomISO ingest branch. Run the guest_slot_watcher (or the quickstart) so the
new slot auto-attaches to the selector.
"""
import sys

from adapters import ZoomIsoMxlAdapter
from contribution_core import ContributionCore

SRC = sys.argv[1] if len(sys.argv) > 1 else None
DST = sys.argv[2] if len(sys.argv) > 2 else '9e111e00-aaaa-4bbb-8ccc-000000000001'
POLICY = sys.argv[3] if len(sys.argv) > 3 else 'align'

if not SRC:
    sys.exit('usage: zoomiso_dryrun.py <fake-source-flow-uuid> <target-slot-uuid> [align|restamp]\n'
             '  (fake-source = any existing v210 flow in the domain, standing in for ZoomISO)')

print(f'ZoomISO DRY-RUN: faking a native-MXL source flow {SRC[:8]}… '
      f'→ slot {DST[:8]}… (timing_policy={POLICY})', flush=True)
print('  this exercises the REAL ZoomIsoMxlAdapter branch (needs_conform=False, mxlsrc).', flush=True)

ContributionCore(
    ZoomIsoMxlAdapter(source_flow_id=SRC, flow_id=DST, label='Zoom 1',
                      timing_policy=POLICY),
    diag_every=150,
).run()
