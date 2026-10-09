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
        code, single = _get(base + "/x-nmos/connection/v1.2/single")
        assert "receivers/" in single and "senders/" in single
        code, recvs = _get(base + "/x-nmos/connection/v1.2/single/receivers/")
        assert code == 200 and len(recvs) == len(nm.SLOT_TO_SENDER)
    finally:
        node.shutdown(); fac.shutdown()


def test_receiver_staged_readable_and_defaults_disabled():
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot2")
        code, staged = _get(base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/staged")
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
        url = base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/staged"
        code, resp = _patch(url, {
            "master_enable": True,
            "sender_id": sid,
            "activation": {"mode": "activate_immediate"},
        })
        assert code == 200, resp
        # the shim translated the activation into a real selector cut to slot 3
        assert _FacilityStub.cuts == [{"slot": 3}], _FacilityStub.cuts
        # and promoted staged -> active
        code, active = _get(base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/active")
        assert active["master_enable"] is True
        assert active["activation"]["mode"] == "activate_immediate"
        assert active["activation"]["activation_time"] is not None
    finally:
        node.shutdown(); fac.shutdown()


def test_patch_staging_without_activation_does_not_cut():
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot1")
        url = base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/staged"
        code, resp = _patch(url, {"master_enable": True, "sender_id": nm.u5("sender", "playout")})
        assert code == 200
        assert _FacilityStub.cuts == []           # staged only — no activation, no cut
        code, active = _get(base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/active")
        assert active["master_enable"] is False    # active unchanged until activated
    finally:
        node.shutdown(); fac.shutdown()


def test_sender_staged_is_read_only():
    base, fac, node = _start_node()
    try:
        sid = nm.u5("sender", "cam")
        req = urllib.request.Request(
            base + f"/x-nmos/connection/v1.2/single/senders/{sid}/staged",
            data=b"{}", headers={"Content-Type": "application/json"}, method="PATCH")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "sender PATCH should be rejected"
        except urllib.error.HTTPError as e:
            assert e.code == 405
    finally:
        node.shutdown(); fac.shutdown()


# ── ADR-001 convergence: MXL flow identity travels in transport_params (v1.2 shape) ──
def test_sender_active_carries_mxl_flow_and_domain_id():
    # A controller reads the Sender's active transport_params to learn what to route.
    base, fac, node = _start_node()
    try:
        sid = nm.u5("sender", "guest1")
        code, active = _get(base + f"/x-nmos/connection/v1.2/single/senders/{sid}/active")
        tp = active["transport_params"][0]
        assert tp["mxl_flow_id"] == nm.SENDER_FLOWS["guest1"][0]   # the real MXL flow uuid
        assert "mxl_domain_id" in tp                               # domain id present (may be str/uuid)
    finally:
        node.shutdown(); fac.shutdown()


def test_patch_routes_by_transport_params_flow_id():
    # The standard IS-05 flow: copy the sender's transport_params onto the receiver, activate.
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot4")                 # slot 4 = guest1
        flow_id = nm.SENDER_FLOWS["guest1"][0]
        url = base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/staged"
        code, resp = _patch(url, {
            "master_enable": True,
            "transport_params": [{"mxl_flow_id": flow_id, "mxl_domain_id": nm.MXL_DOMAIN_ID}],
            "activation": {"mode": "activate_immediate"},
        })
        assert code == 200, resp
        assert _FacilityStub.cuts == [{"slot": 4}], _FacilityStub.cuts   # cut to the guest1 slot
        # active reflects the routed flow id
        code, active = _get(base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/active")
        assert active["transport_params"][0]["mxl_flow_id"] == flow_id
    finally:
        node.shutdown(); fac.shutdown()


def test_patch_disable_is_first_class_and_does_not_cut():
    # IS-05 disable (master_enable=false) must activate cleanly WITHOUT forcing a program cut.
    base, fac, node = _start_node()
    try:
        rid = nm.u5("receiver", "slot5")
        url = base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/staged"
        code, resp = _patch(url, {
            "master_enable": False,
            "activation": {"mode": "activate_immediate"},
        })
        assert code == 200, resp
        assert _FacilityStub.cuts == []                  # disable never cuts program
        code, active = _get(base + f"/x-nmos/connection/v1.2/single/receivers/{rid}/active")
        assert active["master_enable"] is False
        assert active["activation"]["activation_time"] is not None   # but it DID activate
    finally:
        node.shutdown(); fac.shutdown()
