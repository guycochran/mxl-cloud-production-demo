#!/usr/bin/env python3
"""Live WebRTC viewer counter for the MXL health board.

mediamtx's API is disabled (enabling = container recreate = viewer blip), but
its log narrates every session. Follow it, keep the open-session set, write
thumbs/viewers.json where mxl_thumbs merges it into health.json.

Ghost rule: sessions that die without a 'closed' line (e.g. during the 14:02
selector wedge) linger forever in the naive set — so alongside nominal `open`
we report `opened_10m`/`opened_60m` (recent-activity gauges) and drop opens
older than 6h from `open`. For "can I down the box" reads, trust opened_10m.
"""
import json, re, subprocess, time

OUT = '/dev/shm/mxl/domain_1/thumbs/viewers.json'
PAT = re.compile(r'\[WebRTC\] \[session ([0-9a-f]+)\] (created|closed)')
open_s = {}   # sid -> epoch created
opened = []   # epochs of all creates (for the rolling windows)


def write():
    now = time.time()
    while opened and opened[0] < now - 3600:
        opened.pop(0)
    for sid, t in list(open_s.items()):
        if t < now - 6 * 3600:
            del open_s[sid]
    data = {'ts': int(now), 'open': len(open_s),
            'opened_10m': sum(1 for t in opened if t > now - 600),
            'opened_60m': len(opened)}
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    import os
    os.replace(tmp, OUT)


# --since 48h replays history so the open-set converges after a restart,
# then -f follows live. Timestamps in the replay are parsed from the line.
proc = subprocess.Popen(
    ['docker', 'logs', 'mediamtx', '--since', '48h', '-f'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
last_write = 0
for line in proc.stdout:
    m = PAT.search(line)
    now = time.time()
    if m:
        sid, ev = m.groups()
        try:  # line format: 2026/09/11 16:11:07 INF ...
            ts = time.mktime(time.strptime(' '.join(line.split()[:2]), '%Y/%m/%d %H:%M:%S'))
        except Exception:
            ts = now
        if ev == 'created':
            open_s[sid] = ts
            opened.append(ts)
        else:
            open_s.pop(sid, None)
    if now - last_write > 10:
        write()
        last_write = now
