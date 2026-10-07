#!/usr/bin/env python3
"""IS-04 NMOS Node for the MXL cloud facility (BCP-007-03 device side).

Presents the facility as standard NMOS resources so any controller with a
registry (easy-nmos today, Bitfocus Buttons v1.8's built-in registry when
it ships, Lawo/Riedel/Pebble) can DISCOVER it:

  node ─ device "MXL Cloud Switcher"
         ├─ per MXL flow: source + flow + SENDER
         │    transport  urn:x-nmos:transport:mxl        (BCP-007-03)
         │    tags       mxl_domain_id / mxl_flow_id
         └─ per selector slot: RECEIVER (caps video/v210, subscribed to
              its attached sender — live PGM shown in tags)

IS-04 discovery (above) PLUS an IS-05 Connection API shim (/x-nmos/connection/v1.1):
a controller that PATCHes a RECEIVER's /staged with a sender_id + activate triggers a
real selector cut — because our receivers ARE the switcher's input slots and "program"
is just which slot is live. So `activate receiver slotN` == `cut program to slot N`
(POST /api/mxl/input on the facility). This is the piece that turns the node from
"discoverable" into "routable" by a standard NMOS controller (BCP-007-03 §connection).
Senders are read-only here (an MXL sender has no transport file to stage — BCP-007-03).

Set --facility to a backend that exposes POST /api/mxl/input for activation to take
effect; without it, staging still works (IS-05 conformant) but activation is a no-op
cut (logged). Mutating the facility is gated the same way the facility gates it
(X-MXL-Token via MXL_CONTROL_TOKEN).

Usage:
  nmos_node.py [--port 8021] [--href http://THIS_HOST:8021/]
               [--registry http://registry:8010]   # enables registration+heartbeat
               [--facility https://prodbots.com]    # POST /api/mxl/input for IS-05 activation

stdlib only. Peer-to-peer queryable without a registry:
  curl :8021/x-nmos/node/v1.3/senders/
  curl :8021/x-nmos/connection/v1.1/single/receivers/
"""
import argparse
import json
import os
import socket
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NS = uuid.UUID('6ba7b812-9dad-11d1-80b4-00c04fd430c8')  # uuid5 namespace (X500)
MXL_DOMAIN_ID = 'domain_1'   # from the domain's domain_def.json (BCP-007-03
                             # prefers a UUID here — upgrading the def is a
                             # one-line change on the domain host)

# name -> (mxl flow uuid, human label)
SENDER_FLOWS = {
    'cam':      ('ca111e00-aaaa-4bbb-8ccc-000000000001', 'Studio Cam 1'),
    'playout':  ('2f34c189-64bf-5971-993a-332a28a7a6ee', 'Playout'),
    'pattern':  ('6b5d8d68-64ce-56f8-bea2-e79b6c282a86', 'Test Pattern'),
    'cam2':     ('ca222e00-aaaa-4bbb-8ccc-000000000001', 'Studio Cam 2'),
    'guest1':   ('9e111e00-aaaa-4bbb-8ccc-000000000001', 'Guest 1 (SRT contribution)'),
    'guest2':   ('9e222e00-aaaa-4bbb-8ccc-000000000001', 'Guest 2 (SRT contribution)'),
    'layout':   ('1a900700-aaaa-4bbb-8ccc-000000000001', 'SuperSource Layout'),
    'selector_pgm': ('9437652d-20d9-565e-be6e-b98c36067930', 'Selector PGM'),
    'keyer_pgm':    ('5c73394e-85df-50a3-8988-5edde5b5522a', 'Keyer PGM (program)'),
}
# selector slot -> sender name (what each receiver is subscribed to)
SLOT_TO_SENDER = {0: 'cam', 1: 'playout', 2: 'pattern', 3: 'cam2',
                  4: 'guest1', 5: 'guest2', 6: 'layout'}

def u5(kind, name):
    return str(uuid.uuid5(NS, f'mxl-cloud-demo:{kind}:{name}'))

def ver():
    t = time.time()
    return f'{int(t)}:{int((t % 1) * 1e9):09d}'

class Model:
    def __init__(self, args):
        self.args = args
        self.node_id = u5('node', 'facility')
        self.device_id = u5('device', 'switcher')
        self.state = {'input': None, 'pvw': None, 'live': {}}
        self.version = ver()
        self.lock = threading.Lock()
        self._conn = {}   # IS-05 staged/active state, keyed by receiver id
        # receiver id -> slot, for IS-05 PATCH routing
        self._rid_slot = {u5('receiver', f'slot{s}'): s for s in SLOT_TO_SENDER}

    def poll_facility(self):
        while True:
            try:
                # UA header dodges Cloudflare's python-urllib bot rule
                # (CF-1010 — same fix as layout_pgm / tams_shipper)
                req = urllib.request.Request(
                    self.args.facility + '/api/mxl/status',
                    headers={'User-Agent': 'mxl-nmos-node/1.0'})
                with urllib.request.urlopen(req, timeout=8) as r:
                    st = json.load(r)
                with self.lock:
                    changed = (st.get('input') != self.state.get('input')
                               or st.get('pvw') != self.state.get('pvw'))
                    self.state = {
                        'input': st.get('input'), 'pvw': st.get('pvw'),
                        'live': {s['slot']: s['live'] for s in st.get('slots', [])},
                    }
                    if changed:
                        self.version = ver()
            except Exception:
                pass
            time.sleep(3)

    # ---- resource builders -------------------------------------------------
    def base(self, kind, name, label, desc=''):
        return {'id': u5(kind, name), 'version': self.version,
                'label': label, 'description': desc, 'tags': {}}

    def self_(self):
        d = self.base('node', 'facility', 'MXL Cloud Facility',
                      'mxl-cloud-production-demo — first production MXL deployment')
        d['id'] = self.node_id
        d.update({
            'href': self.args.href,
            'hostname': socket.gethostname(),
            'caps': {},
            'api': {'versions': ['v1.3'],
                    'endpoints': [{'host': self.args.href.split('//')[1].split(':')[0].rstrip('/'),
                                   'port': self.args.port, 'protocol': 'http'}]},
            'services': [], 'clocks': [{'name': 'clk0', 'ref_type': 'internal'}],
            'interfaces': [],
        })
        return d

    def device(self):
        d = self.base('device', 'switcher', 'MXL Cloud Switcher',
                      'uncompressed v210 shared-memory switcher (Azure)')
        d['id'] = self.device_id
        # Advertise the IS-05 Connection API so a controller knows we are routable.
        conn_href = self.args.href.rstrip('/') + '/x-nmos/connection/v1.1/'
        d.update({'type': 'urn:x-nmos:device:generic', 'node_id': self.node_id,
                  'controls': [{'href': conn_href,
                                'type': 'urn:x-nmos:control:sr-ctrl/v1.1'}],
                  'senders': [u5('sender', n) for n in SENDER_FLOWS],
                  'receivers': [u5('receiver', f'slot{s}') for s in SLOT_TO_SENDER]})
        return d

    def sources(self):
        out = []
        for n, (fid, label) in SENDER_FLOWS.items():
            d = self.base('source', n, label)
            d.update({'format': 'urn:x-nmos:format:video', 'caps': {},
                      'device_id': self.device_id, 'parents': [],
                      'clock_name': 'clk0',
                      'grain_rate': {'numerator': 30, 'denominator': 1}})
            out.append(d)
        return out

    def flows(self):
        out = []
        for n, (fid, label) in SENDER_FLOWS.items():
            d = self.base('flow', n, label)
            d.update({'format': 'urn:x-nmos:format:video',
                      'source_id': u5('source', n), 'device_id': self.device_id,
                      'parents': [], 'media_type': 'video/v210',
                      'frame_width': 1920, 'frame_height': 1080,
                      'colorspace': 'BT709', 'interlace_mode': 'progressive',
                      'grain_rate': {'numerator': 30, 'denominator': 1}})
            out.append(d)
        return out

    def senders(self):
        out = []
        with self.lock:
            pgm = self.state.get('input')
        for n, (fid, label) in SENDER_FLOWS.items():
            d = self.base('sender', n, label)
            slot = next((s for s, sn in SLOT_TO_SENDER.items() if sn == n), None)
            d['tags'] = {'urn:x-mxl:tag:mxl_domain_id': [MXL_DOMAIN_ID],
                         'urn:x-mxl:tag:mxl_flow_id': [fid]}
            if slot is not None and slot == pgm:
                d['tags']['urn:x-nmos:tag:grouphint/v1.0'] = ['PGM']
            d.update({'flow_id': u5('flow', n), 'device_id': self.device_id,
                      'transport': 'urn:x-nmos:transport:mxl',
                      'manifest_href': None,   # BCP-007-03: no transport file
                      'interface_bindings': [],
                      'subscription': {'receiver_id': None, 'active': True}})
            out.append(d)
        return out

    def receivers(self):
        out = []
        with self.lock:
            live = dict(self.state.get('live', {}))
            pgm = self.state.get('input')
            pvw = self.state.get('pvw')
        for slot, sender_name in SLOT_TO_SENDER.items():
            d = self.base('receiver', f'slot{slot}',
                          f'Switcher input {slot} ({sender_name})')
            role = 'PGM' if slot == pgm else ('PVW' if slot == pvw else '')
            d['tags'] = {'urn:x-mxl:tag:mxl_domain_id': [MXL_DOMAIN_ID]}
            if role:
                d['tags']['urn:x-nmos:tag:grouphint/v1.0'] = [role]
            d.update({'device_id': self.device_id,
                      'format': 'urn:x-nmos:format:video',
                      'caps': {'media_types': ['video/v210']},
                      'transport': 'urn:x-nmos:transport:mxl',
                      'interface_bindings': [],
                      'subscription': {'sender_id': u5('sender', sender_name),
                                       'active': bool(live.get(slot, False))}})
            out.append(d)
        return out

    # ---- IS-05 Connection API (BCP-007-03 §connection) ---------------------
    # Our receivers are the switcher's input slots; activating one = cutting PGM to
    # that slot. Staged/active params per receiver live here. An MXL transport has no
    # transport_params beyond the domain+flow (carried as IS-04 tags), so the IS-05
    # params are minimal: master_enable + the sender_id being routed.
    def _recv_conn(self, slot):
        """Lazily-initialised staged/active connection record for a receiver slot."""
        rid = u5('receiver', f'slot{slot}')
        rec = self._conn.setdefault(rid, {
            'staged': {'master_enable': False, 'sender_id': None,
                       'activation': {'mode': None, 'requested_time': None,
                                      'activation_time': None},
                       'transport_params': [{}]},
            'active': {'master_enable': False, 'sender_id': None,
                       'activation': {'mode': None, 'requested_time': None,
                                      'activation_time': None},
                       'transport_params': [{}]},
        })
        return rid, rec

    def _ctl_headers(self):
        h = {'Content-Type': 'application/json', 'User-Agent': 'mxl-nmos-node/1.0'}
        tok = os.environ.get('MXL_CONTROL_TOKEN', '').strip()
        if tok:
            h['X-MXL-Token'] = tok
        return h

    def activate_receiver(self, slot, sender_id):
        """Apply an IS-05 activation: cut the switcher's program to this slot.
        Returns (ok, detail). Never raises — a failed cut becomes a 500 to the
        controller, not a crash."""
        # The staged sender must be one of ours (or None to disconnect). We route by
        # SLOT — the sender_id is advisory here (the receiver's slot is fixed wiring).
        try:
            req = urllib.request.Request(
                self.args.facility + '/api/mxl/input',
                data=json.dumps({'slot': slot}).encode(),
                headers=self._ctl_headers(), method='POST')
            with urllib.request.urlopen(req, timeout=8) as r:
                body = r.read()[:200].decode('utf-8', 'replace')
                print(f'IS-05 activate: receiver slot{slot} -> PGM (sender {sender_id}) '
                      f'=> {r.status} {body}', flush=True)
                return True, body
        except Exception as e:
            print(f'IS-05 activate slot{slot} FAILED: {e}', flush=True)
            return False, str(e)

    def conn_senders(self):
        # Senders are read-only in BCP-007-03 (no transport file to stage).
        return [u5('sender', n) for n in SENDER_FLOWS]

    def conn_receivers(self):
        return [u5('receiver', f'slot{s}') for s in SLOT_TO_SENDER]

MODEL = None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.rstrip('/')
        m = MODEL
        table = {
            '/x-nmos': ['node/', 'connection/'],
            '/x-nmos/node': ['v1.3/'],
            '/x-nmos/node/v1.3': ['self/', 'devices/', 'sources/', 'flows/',
                                  'senders/', 'receivers/'],
            '/x-nmos/connection': ['v1.1/'],
            '/x-nmos/connection/v1.1': ['single/', 'bulk/'],
            '/x-nmos/connection/v1.1/single': ['senders/', 'receivers/'],
        }
        if p in table:
            return self.send_json(table[p])
        # ---- IS-05 Connection API (GET) ----
        if self.conn_get(p, m):
            return
        col = {'/x-nmos/node/v1.3/devices': lambda: [m.device()],
               '/x-nmos/node/v1.3/sources': m.sources,
               '/x-nmos/node/v1.3/flows': m.flows,
               '/x-nmos/node/v1.3/senders': m.senders,
               '/x-nmos/node/v1.3/receivers': m.receivers}
        if p == '/x-nmos/node/v1.3/self':
            return self.send_json(m.self_())
        if p in col:
            return self.send_json(col[p]())
        for base, fn in col.items():
            if p.startswith(base + '/'):
                rid = p[len(base) + 1:]
                for item in fn():
                    if item['id'] == rid:
                        return self.send_json(item)
                return self.send_json({'code': 404, 'error': 'not found'}, 404)
        self.send_json({'code': 404, 'error': 'not found'}, 404)

    # ---- IS-05 Connection API: GET of single/{senders,receivers}/... --------
    def conn_get(self, p, m):
        """Handle a GET under /x-nmos/connection/v1.1/single/. Returns True if it
        matched (and responded), False to fall through to the IS-04 handler."""
        S = '/x-nmos/connection/v1.1/single/senders'
        R = '/x-nmos/connection/v1.1/single/receivers'
        if p == S:
            self.send_json([s + '/' for s in m.conn_senders()]); return True
        if p == R:
            self.send_json([r + '/' for r in m.conn_receivers()]); return True
        # senders are read-only (no transport file): expose the standard sub-resources.
        if p.startswith(S + '/'):
            rest = p[len(S) + 1:]
            sid, _, leaf = rest.partition('/')
            if sid not in m.conn_senders():
                self.send_json({'code': 404, 'error': 'no such sender'}, 404); return True
            if leaf == '':
                self.send_json(['constraints/', 'staged/', 'active/', 'transporttype/']); return True
            if leaf == 'transporttype':
                self.send_json('urn:x-nmos:transport:mxl'); return True
            if leaf == 'constraints':
                self.send_json([{}]); return True
            if leaf in ('staged', 'active'):
                self.send_json({'master_enable': True, 'receiver_id': None,
                                'activation': {'mode': None, 'requested_time': None,
                                               'activation_time': None},
                                'transport_params': [{}]}); return True
            self.send_json({'code': 404, 'error': 'not found'}, 404); return True
        if p.startswith(R + '/'):
            rest = p[len(R) + 1:]
            rid, _, leaf = rest.partition('/')
            if rid not in m._rid_slot:
                self.send_json({'code': 404, 'error': 'no such receiver'}, 404); return True
            _, rec = m._recv_conn(m._rid_slot[rid])
            if leaf == '':
                self.send_json(['constraints/', 'staged/', 'active/', 'transporttype/']); return True
            if leaf == 'transporttype':
                self.send_json('urn:x-nmos:transport:mxl'); return True
            if leaf == 'constraints':
                self.send_json([{}]); return True
            if leaf == 'staged':
                self.send_json(rec['staged']); return True
            if leaf == 'active':
                self.send_json(rec['active']); return True
            self.send_json({'code': 404, 'error': 'not found'}, 404); return True
        return False

    # ---- IS-05 Connection API: PATCH single/receivers/<id>/staged -----------
    def do_PATCH(self):
        m = MODEL
        p = self.path.rstrip('/')
        R = '/x-nmos/connection/v1.1/single/receivers'
        if not (p.startswith(R + '/') and p.endswith('/staged')):
            return self.send_json({'code': 405, 'error': 'only receiver /staged is patchable '
                                   '(senders are read-only on an MXL transport)'}, 405)
        rid = p[len(R) + 1:-len('/staged')]
        if rid not in m._rid_slot:
            return self.send_json({'code': 404, 'error': 'no such receiver'}, 404)
        try:
            n = int(self.headers.get('Content-Length', 0))
            patch = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self.send_json({'code': 400, 'error': 'bad JSON body'}, 400)
        slot = m._rid_slot[rid]
        _, rec = m._recv_conn(slot)
        st = rec['staged']
        # merge the PATCH into staged (IS-05 is a partial update)
        if 'master_enable' in patch:
            st['master_enable'] = bool(patch['master_enable'])
        if 'sender_id' in patch:
            st['sender_id'] = patch['sender_id']
        if 'transport_params' in patch:
            st['transport_params'] = patch['transport_params']
        act = patch.get('activation') or {}
        mode = act.get('mode')
        st['activation'] = {'mode': mode, 'requested_time': act.get('requested_time'),
                            'activation_time': None}
        # Only immediate activation is supported (no scheduled/relative PTP here).
        if mode == 'activate_immediate':
            if st['master_enable']:
                ok, detail = m.activate_receiver(slot, st['sender_id'])
                if not ok:
                    return self.send_json({'code': 500, 'error': f'activation failed: {detail}'}, 500)
            # promote staged -> active; stamp activation_time
            st['activation']['activation_time'] = ver()
            rec['active'] = json.loads(json.dumps(st))   # deep copy
            rec['active']['activation']['mode'] = 'activate_immediate'
            with m.lock:
                m.version = ver()
            return self.send_json(st, 200)
        elif mode in ('activate_scheduled_absolute', 'activate_scheduled_relative'):
            return self.send_json({'code': 501, 'error': 'scheduled activation not supported '
                                   '(immediate only on this MXL shim)'}, 501)
        # staging without activation
        return self.send_json(st, 200)

def register_loop(m, registry):
    api = registry.rstrip('/') + '/x-nmos/registration/v1.3'
    def post(kind, data):
        req = urllib.request.Request(api + '/resource',
            data=json.dumps({'type': kind, 'data': data}).encode(),
            headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=8).read()
    def register_all():
        post('node', m.self_())
        post('device', m.device())
        for s in m.sources(): post('source', s)
        for f in m.flows(): post('flow', f)
        for s in m.senders(): post('sender', s)
        for r in m.receivers(): post('receiver', r)
        print(f'registered with {registry}', flush=True)
    while True:
        try:
            register_all()
            while True:
                time.sleep(5)
                req = urllib.request.Request(api + f'/health/nodes/{m.node_id}',
                                             data=b'', method='POST')
                urllib.request.urlopen(req, timeout=8).read()
        except Exception as e:
            print(f'registry: {e} — retrying in 10s', flush=True)
            time.sleep(10)

def main():
    global MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8021)
    ap.add_argument('--href', default=None)
    ap.add_argument('--registry', default=None)
    ap.add_argument('--facility', default=os.environ.get('MXL_BACKEND_URL', 'https://prodbots.com'))
    args = ap.parse_args()
    if not args.href:
        args.href = f'http://{socket.gethostbyname(socket.gethostname())}:{args.port}/'
    MODEL = Model(args)
    threading.Thread(target=MODEL.poll_facility, daemon=True).start()
    if args.registry:
        threading.Thread(target=register_loop, args=(MODEL, args.registry),
                         daemon=True).start()
    print(f'IS-04 + IS-05 node on :{args.port} (href {args.href})'
          + (f' -> registry {args.registry}' if args.registry else ' (peer-to-peer)')
          + f'; IS-05 activation routes to {args.facility}/api/mxl/input',
          flush=True)
    ThreadingHTTPServer(('0.0.0.0', args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
