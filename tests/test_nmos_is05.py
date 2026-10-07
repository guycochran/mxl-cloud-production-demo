"""IS-05 Connection API shim on the NMOS node (Tier 3.3).

Drives the real stdlib HTTP server on a loopback port, with the 'facility' stubbed by
a second local server that records the cut. Asserts: the connection tree is exposed,
a receiver's /staged is readable, and a PATCH with activate_immediate both (a) POSTs
/api/mxl/input {slot:N} to the facility and (b) promotes staged -> active.

No GStreamer, no real MXL domain — nmos_node is stdlib-only.
"""
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import nmos_node as nm  # noqa: E402


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _FacilityStub(BaseHTTPRequestHandler):
    cuts = []  # class-level record of {slot:...} bodies POSTed to /api/mxl/input

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.rstrip("/") == "/api/mxl/input":
            _FacilityStub.cuts.append(body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        # the node's poll_facility hits /api/mxl/status; return an empty-ish state
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"input":null,"pvw":null,"slots":[]}')


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read())


def _patch(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="PATCH")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def _start_node():
    """Boot a node + facility stub; return (base_url, facility_stub_server, node_server)."""
    _FacilityStub.cuts = []
    fport = _free_port()
    fac = ThreadingHTTPServer(("127.0.0.1", fport), _FacilityStub)
    threading.Thread(target=fac.serve_forever, daemon=True).start()

    nport = _free_port()

    class Args:
        port = nport
        href = f"http://127.0.0.1:{nport}/"
        registry = None
        facility = f"http://127.0.0.1:{fport}"

    nm.MODEL = nm.Model(Args())
    node = ThreadingHTTPServer(("127.0.0.1", nport), nm.Handler)
    threading.Thread(target=node.serve_forever, daemon=True).start()
    time.sleep(0.2)
    return f"http://127.0.0.1:{nport}", fac, node


def test_connection_api_tree_and_receivers_listed():
    base, fac, node = _start_node()
    try:
        code, root = _get(base + "/x-nmos")
        assert code == 200 and "connection/" in root
        code, single = _get(base + "/x-nmos/connection/v1.1/single")
        assert "receivers/" in single and "senders/" in single
        code, recvs = _get(base + "/x-nmos/connection/v1.1/single/receivers/")
        assert code == 200 and len(recvs) == len(nm.SLOT_TO_SENDER)
    finally:
        node.shutdown(); fac.shutdown()


def test_receiver_staged_readable_and_defaults_disabled():
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot2")
        code, staged = _get(base + f"/x-nmos/connection/v1.1/single/receivers/{rid}/staged")
        assert code == 200
        assert staged["master_enable"] is False
        assert staged["activation"]["mode"] is None
    finally:
        node.shutdown(); fac.shutdown()


def test_patch_activate_immediate_cuts_program_to_that_slot():
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot3")          # slot 3 = cam2
        sid = nm.u5("sender", "cam2")
        url = base + f"/x-nmos/connection/v1.1/single/receivers/{rid}/staged"
        code, resp = _patch(url, {
            "master_enable": True,
            "sender_id": sid,
            "activation": {"mode": "activate_immediate"},
        })
        assert code == 200, resp
        # the shim translated the activation into a real selector cut to slot 3
        assert _FacilityStub.cuts == [{"slot": 3}], _FacilityStub.cuts
        # and promoted staged -> active
        code, active = _get(base + f"/x-nmos/connection/v1.1/single/receivers/{rid}/active")
        assert active["master_enable"] is True
        assert active["activation"]["mode"] == "activate_immediate"
        assert active["activation"]["activation_time"] is not None
    finally:
        node.shutdown(); fac.shutdown()


def test_patch_staging_without_activation_does_not_cut():
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot1")
        url = base + f"/x-nmos/connection/v1.1/single/receivers/{rid}/staged"
        code, resp = _patch(url, {"master_enable": True, "sender_id": nm.u5("sender", "playout")})
        assert code == 200
        assert _FacilityStub.cuts == []           # staged only — no activation, no cut
        code, active = _get(base + f"/x-nmos/connection/v1.1/single/receivers/{rid}/active")
        assert active["master_enable"] is False    # active unchanged until activated
    finally:
        node.shutdown(); fac.shutdown()


def test_sender_staged_is_read_only():
    base, fac, node = _start_node()
    try:
        sid = nm.u5("sender", "cam")
        req = urllib.request.Request(
            base + f"/x-nmos/connection/v1.1/single/senders/{sid}/staged",
            data=b"{}", headers={"Content-Type": "application/json"}, method="PATCH")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "sender PATCH should be rejected"
        except urllib.error.HTTPError as e:
            assert e.code == 405
    finally:
        node.shutdown(); fac.shutdown()
