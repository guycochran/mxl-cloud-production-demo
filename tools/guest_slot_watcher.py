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
  PREFER_STABLE     "1" (default) to point a guest slot at its STABLE flow when
                    one exists; "0" to always use the volatile flow
  STABLE_SUFFIX     label suffix identifying a guest's stable flow (default " Stable")

STABILIZED SLOTS (folds in flow_stabilizer.py): a guest's RAW flow is recreated on
every reconnect (cellular drop, Larix stop/start), and on MXL v1.1.0 a recreated
flow permanently wedges every downstream reader — so without help the watcher must
re-attach the whole selector on each reconnect (which also reverts active-input).
flow_stabilizer.py fixes that at the source: it reads the volatile "Guest N" flow
and writes a "Guest N Stable" flow created once and never recreated, swapping only
its reader in-place. When such a stable flow is present this watcher wires THAT into
the slot, so a reconnect is invisible to the flow set → no re-attach churn, no cut
revert. Falls back to the volatile flow when no stabilizer is running, so this is
purely additive. Run a stabilizer per guest to get the benefit:
  flow_stabilizer.py guest1 <guest1-uuid> <guest1-stable-uuid> "Guest 1 Stable"
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
PREFER_STABLE = os.environ.get('PREFER_STABLE', '1') != '0'
STABLE_SUFFIX = os.environ.get('STABLE_SUFFIX', ' Stable')


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


def guest_flow(fm, lbl):
    """UUID to wire into a guest's slot, or None if the guest isn't available.
    Prefers the STABLE flow ("<label> Stable") when PREFER_STABLE and one exists —
    that flow persists across reconnects, so the slot never needs re-attaching when
    the guest's raw flow is recreated. Falls back to the volatile flow."""
    if PREFER_STABLE:
        stable = fm.get(lbl + STABLE_SUFFIX)
        if stable is not None:
            return stable, True
    raw = fm.get(lbl)
    return (raw, False) if raw is not None else (None, False)


def selector_inputs(fm):
    """Fixed-length UUID list, one per SLOT_LABELS entry, order preserved. An absent
    guest's slot is padded with the SAFE_LABEL flow so indices never move. A present
    guest prefers its stable flow (see guest_flow). Returns (uuids, present_guests)
    or (None, _) if the base/safe flow isn't up yet."""
    safe = fm.get(SAFE_LABEL)
    if safe is None:
        return None, []
    uuids, present = [], []
    for lbl in SLOT_LABELS:
        if lbl in GUEST_LABELS:
            uuid, is_stable = guest_flow(fm, lbl)
            if uuid is not None:
                uuids.append(uuid)
                present.append(lbl + (' (stable)' if is_stable else ''))
            else:
                uuids.append(safe)   # absent guest -> hold the slot with the safe source
        elif lbl in fm:
            uuids.append(fm[lbl])
        else:
            uuids.append(safe)   # base label not up yet -> safe placeholder
    return uuids, present


def write_slot_map(fm):
    """Publish {slot_index: label} so external controllers read identity, not guess
    transient numeric positions. A slot currently padded reads as 'LABEL (idle)'."""
    m = {}
    for i, lbl in enumerate(SLOT_LABELS):
        if lbl in GUEST_LABELS:
            uuid, is_stable = guest_flow(fm, lbl)
            if uuid is not None:
                m[str(i)] = f'{lbl} (stable)' if is_stable else lbl
            else:
                m[str(i)] = f'{lbl} (idle)'
        else:
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
    print(f'  prefer-stable={"on" if PREFER_STABLE else "off"}: guest slots point at '
          f'"<guest>{STABLE_SUFFIX}" flows when present (reconnects then cause no '
          f're-attach).', flush=True)
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
