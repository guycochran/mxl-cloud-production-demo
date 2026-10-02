#!/usr/bin/env python3
"""guest_slot_watcher — backend-free selector re-attach for the quickstart tier.

The live demo re-attaches the selector via the Express backend's /api/mxl/repair
when a guest flow appears. The quickstart has no backend. This watcher is the
self-contained equivalent: it polls the MXL domain for the fixed base flows plus
any Guest flows, and whenever the set of present flows CHANGES it re-issues the
selector's /pipeline/start (directly at :9604) with the current input list — so a
guest who connects a minute after boot becomes a live, cuttable slot hands-off.

Why a watcher and not pre-wiring: an idle guest flow cannot exist — rtspsrc 404s
with no publisher, so the guest-ingest pipeline only builds (and the flow only
appears) once a phone actually streams. Verified on a VM 2026-10-02.

Flow→UUID discovery uses mxl-info inside the input-selector container (same method
quickstart.sh uses). Stdlib only; runs on the bare host.

Env:
  SELECTOR_PORT     default 9604
  SELECTOR_CONTAINER default input-selector  (where mxl-info lives)
  MXL_DOMAIN        default /mxl-domain       (path inside that container)
  BASE_LABELS       comma list, always slots 0..N-1  (default "Pattern Video,Clip Video")
  GUEST_LABELS      comma list, appended as they appear (default "Guest 1,Guest 2")
  POLL_SECONDS      default 3
"""
import json
import os
import subprocess
import time
import urllib.request

SEL_PORT = os.environ.get('SELECTOR_PORT', '9604')
SEL_CTR = os.environ.get('SELECTOR_CONTAINER', 'input-selector')
DOMAIN = os.environ.get('MXL_DOMAIN', '/mxl-domain')
BASE_LABELS = [s for s in os.environ.get('BASE_LABELS', 'Pattern Video,Clip Video').split(',') if s]
GUEST_LABELS = [s for s in os.environ.get('GUEST_LABELS', 'Guest 1,Guest 2').split(',') if s]
POLL = int(os.environ.get('POLL_SECONDS', '3'))


def flow_map():
    """label -> uuid for every video flow currently in the domain."""
    try:
        out = subprocess.run(
            ['docker', 'exec', SEL_CTR, 'sh', '-c',
             f'/opt/mxl/tools/mxl-info/mxl-info -d {DOMAIN} -l'],
            capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        print(f'mxl-info failed: {e}', flush=True)
        return {}
    m = {}
    for line in out.splitlines():
        # lines look like:  "   Video : <uuid> - <label>"
        s = line.strip()
        if ' - ' in s and ' : ' in s:
            left, label = s.rsplit(' - ', 1)
            parts = left.split(' : ')
            if len(parts) == 2:
                uuid = parts[1].strip()
                if len(uuid) == 36:
                    m[label.strip()] = uuid
    return m


def selector_inputs(fm):
    """Ordered UUID list: base labels first (fixed slots), then any guests present."""
    uuids = []
    for lbl in BASE_LABELS + GUEST_LABELS:
        if lbl in fm:
            uuids.append(fm[lbl])
    return uuids


def restart_selector(uuids):
    body = json.dumps({
        'domain_path': DOMAIN,
        'input_flow_uuids': uuids,
        'grouphint': 'Selector',
        'description': 'program out',
        'label': 'Selector PGM',
    }).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{SEL_PORT}/pipeline/start',
                                 data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def main():
    print(f'guest_slot_watcher: base={BASE_LABELS} guests={GUEST_LABELS} '
          f'selector=:{SEL_PORT} poll={POLL}s', flush=True)
    last = None
    while True:
        fm = flow_map()
        uuids = selector_inputs(fm)
        # only act once the base slots exist (selector already started by quickstart)
        base_ready = all(lbl in fm for lbl in BASE_LABELS)
        if base_ready and uuids != last:
            present_guests = [g for g in GUEST_LABELS if g in fm]
            try:
                st = restart_selector(uuids)
                print(f're-attached selector -> {len(uuids)} inputs '
                      f'(guests live: {present_guests or "none"}) [HTTP {st}]', flush=True)
                last = uuids
            except Exception as e:
                print(f'selector re-attach failed: {e} (will retry)', flush=True)
        time.sleep(POLL)


if __name__ == '__main__':
    main()
