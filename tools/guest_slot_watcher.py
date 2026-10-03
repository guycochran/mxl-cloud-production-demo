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


# The SLOT LAYOUT is FIXED: one slot per label in BASE_LABELS + GUEST_LABELS, always
# in that order, always that length. A guest that isn't streaming yet does NOT collapse
# the list (which used to shift every later guest's button number) — its slot is held by
# a SAFE placeholder flow until the phone connects. So "Guest 2" is slot 3 whether or not
# "Guest 1" is live, and a control surface / Companion / operator can trust slot numbers.
SLOT_LABELS = BASE_LABELS + GUEST_LABELS
SAFE_LABEL = os.environ.get('SAFE_LABEL', BASE_LABELS[0] if BASE_LABELS else 'Pattern Video')
SLOT_MAP_FILE = os.environ.get('SLOT_MAP_FILE', '/tmp/mxl-slot-map.json')


def selector_inputs(fm):
    """Fixed-length UUID list, one per SLOT_LABELS entry, order preserved. An absent
    guest's slot is padded with the SAFE_LABEL flow so indices never move. Returns
    (uuids, present_guests) or (None, _) if the base/safe flow isn't up yet."""
    safe = fm.get(SAFE_LABEL)
    if safe is None:
        return None, []
    uuids, present = [], []
    for lbl in SLOT_LABELS:
        if lbl in fm:
            uuids.append(fm[lbl])
            if lbl in GUEST_LABELS:
                present.append(lbl)
        else:
            uuids.append(safe)   # hold the slot with the safe source
    return uuids, present


def write_slot_map(fm):
    """Publish {slot_index: label} so external controllers read identity, not guess
    transient numeric positions. A slot currently padded reads as 'LABEL (idle)'."""
    m = {}
    for i, lbl in enumerate(SLOT_LABELS):
        m[str(i)] = lbl if lbl in fm else f'{lbl} (idle)'
    try:
        with open(SLOT_MAP_FILE, 'w') as f:
            json.dump(m, f)
    except Exception as e:
        print(f'slot-map write failed: {e}', flush=True)


def get_active_slot():
    """Current selector active-input index, or None."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{SEL_PORT}/pipeline/status',
                                    timeout=5) as r:
            return json.loads(r.read() or b'{}').get('active_input')
    except Exception:
        return None


def set_active_slot(slot):
    body = json.dumps({'slot': slot}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{SEL_PORT}/pipeline/active-input',
                                 data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


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
    print(f'guest_slot_watcher: slots={SLOT_LABELS} safe="{SAFE_LABEL}" '
          f'selector=:{SEL_PORT} poll={POLL}s map={SLOT_MAP_FILE}', flush=True)
    print('  slot numbers are STABLE: an absent guest is held by the safe source, '
          'not collapsed.', flush=True)
    last = None
    while True:
        fm = flow_map()
        uuids, present_guests = selector_inputs(fm)
        if uuids is None:
            time.sleep(POLL)
            continue  # base/safe flow not up yet (selector started by quickstart)
        write_slot_map(fm)
        if uuids != last:
            # Because the slot LAYOUT is fixed, a restart cannot renumber a live cut —
            # but the selector reverts active-input to 0 on /pipeline/start, so read the
            # operator's current slot first and restore it afterward. If that slot is a
            # now-idle (padded) guest, the picture is the safe source either way.
            active = get_active_slot()
            try:
                st = restart_selector(uuids)
                if active is not None and active != 0:
                    try:
                        set_active_slot(active)
                        restored = f', restored active slot {active}'
                    except Exception as e:
                        restored = f', WARN could not restore slot {active}: {e}'
                else:
                    restored = ''
                print(f're-attached selector -> {len(uuids)} fixed slots '
                      f'(guests live: {present_guests or "none"}) [HTTP {st}]{restored}',
                      flush=True)
                last = uuids
            except Exception as e:
                print(f'selector re-attach failed: {e} (will retry)', flush=True)
        time.sleep(POLL)


if __name__ == '__main__':
    main()
