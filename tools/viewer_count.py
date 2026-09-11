#!/usr/bin/env python3
"""Live WebRTC viewer counter + 24h traffic record for the MXL health board.

mediamtx's API is disabled (enabling = container recreate = viewer blip), but
its log narrates every session. Follow it, keep the open-session set and a
24h record of session opens (with source IPs), write thumbs/viewers.json
where mxl_thumbs merges it into health.json.

Ghost rule: sessions that die without a 'closed' line (e.g. during the 14:02
selector wedge) linger forever in the naive set — so alongside nominal `open`
we report `opened_10m`/`opened_60m` (recent-activity gauges) and drop opens
older than 6h from `open`. For "can I down the box" reads, trust opened_10m.

A restart replays 48h of log before following, so the 24h traffic record
survives restarts.
"""
import json, re, subprocess, time

OUT = '/dev/shm/mxl/domain_1/thumbs/viewers.json'
PAT = re.compile(r'\[WebRTC\] \[session ([0-9a-f]+)\] (created|closed)')
IP_PAT = re.compile(r'created by ([0-9.]+):')
open_s = {}   # sid -> epoch created
opened = []   # (epoch, ip) of every create in the last 24h — the traffic record


def write():
    now = time.time()
    while opened and opened[0][0] < now - 24 * 3600:
        opened.pop(0)
    for sid, t in list(open_s.items()):
        if t < now - 6 * 3600:
            del open_s[sid]
    hourly = {}
    for t, ip in opened:
        hb = int(t // 3600) * 3600
        b = hourly.setdefault(hb, {'n': 0, 'ips': set()})
        b['n'] += 1
        if ip:
            b['ips'].add(ip)
    data = {'ts': int(now), 'open': len(open_s),
            'opened_10m': sum(1 for t, _ in opened if t > now - 600),
            'opened_60m': sum(1 for t, _ in opened if t > now - 3600),
            'sessions_24h': len(opened),
            'ips_24h': len({ip for _, ip in opened if ip}),
            'hourly': [{'t': hb, 'n': b['n'], 'ips': len(b['ips'])}
                       for hb, b in sorted(hourly.items())]}
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    import os
    os.replace(tmp, OUT)


# --since 48h replays history so the open-set + traffic record converge after
# a restart, then -f follows live. Timestamps are parsed from the line.
# bufsize=1 (line-buffered) is essential: with default block buffering the 48h
# replay sits unflushed in the pipe buffer whenever follow-mode goes quiet
# (no viewers), so the counter would stall at zero until live traffic filled
# the buffer. Line buffering flushes each line as it arrives.
proc = subprocess.Popen(
    ['docker', 'logs', 'mediamtx', '--since', '48h', '-f'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
# NOTE: `for line in proc.stdout` has its OWN 8KB read-ahead buffer that
# ignores bufsize — during a quiet follow it strands the replayed history.
# iter(readline, '') reads a line at a time with no hidden buffer.
last_write = 0
for line in iter(proc.stdout.readline, ''):
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
            ipm = IP_PAT.search(line)
            opened.append((ts, ipm.group(1) if ipm else None))
        else:
            open_s.pop(sid, None)
    if now - last_write > 10:
        write()
        last_write = now
