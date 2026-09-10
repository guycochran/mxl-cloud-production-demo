#!/usr/bin/env python3
"""Patch an mxl-fabrics-demo target.json for NAT/non-routed networks.

TargetInfo embeds the target's LOCAL bind sockaddr inside the base64
fabricAddress; when the initiator must connect via a PUBLIC IP (NAT,
cross-cloud, conference network with port-forward), rewrite bytes [4:8]
of the decoded sockaddr to the public IPv4. Port (bytes [2:4]) unchanged.
Field-proven VM1->VM3 across isolated VNets at 30 grains/s (dmf-mxl #714).

Usage: patch-target-ip.py <target.json> <public-ip> [out.json]
"""
import base64
import ipaddress
import json
import sys

src, ip = sys.argv[1], sys.argv[2]
out = sys.argv[3] if len(sys.argv) > 3 else src.replace('.json', '-patched.json')
d = json.load(open(src))
raw = bytearray(base64.b64decode(d['fabricAddress']))
old = ipaddress.IPv4Address(bytes(raw[4:8]))
raw[4:8] = ipaddress.IPv4Address(ip).packed
d['fabricAddress'] = base64.b64encode(bytes(raw)).decode()
json.dump(d, open(out, 'w'))
print(f'{old} -> {ip}  (port bytes untouched)  wrote {out}')
