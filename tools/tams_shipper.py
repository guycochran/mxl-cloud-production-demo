#!/usr/bin/env python3
"""MXL → TAMS bridge (shipper half). Runs on VM2.

Watches the spool of 1s MPEG-TS segments cut from the fabric-delivered MXL
program and registers each in the TAMS store on VM3:
  POST /flows/{id}/storage  -> presigned put_url + object_id
  PUT  <put_url>            -> segment bytes (S3/MinIO)
  POST /flows/{id}/segments -> {object_id, timerange}
Timeranges come from the segmenter's epoch-stamped filenames (hosts are NTP-
synced — capture timing preserved end to end, the white paper's whole point).
Retries forever; safe to start before the TAMS ports are reachable.
"""
import glob
import json
import os
import time
import urllib.request

TAMS = os.environ.get('TAMS_URL', 'http://20.112.83.140:8000')
FLOW = os.environ.get('TAMS_FLOW', '7a350001-aaaa-4bbb-8ccc-000000000001')
SPOOL = os.environ.get('SPOOL', '/srv/tams-spool')
SEG_SECS = 1

def req(method, url, data=None, ctype='application/json', timeout=15):
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header('Content-Type', ctype)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read()

def ship(path):
    epoch = int(os.path.basename(path).split('-')[1].split('.')[0])
    with open(path, 'rb') as f:
        payload = f.read()
    if len(payload) < 10_000:            # runt segment (startup) — skip
        os.remove(path)
        return f'skip runt {os.path.basename(path)}'
    _, body = req('POST', f'{TAMS}/flows/{FLOW}/storage', data=b'{}')
    obj = json.loads(body)['media_objects'][0]
    put = obj['put_url']
    req('PUT', put['url'], data=payload, ctype=put.get('content-type', 'application/octet-stream'), timeout=30)
    seg = {'object_id': obj['object_id'],
           'timerange': f'[{epoch}:0_{epoch + SEG_SECS}:0)'}
    req('POST', f'{TAMS}/flows/{FLOW}/segments', data=json.dumps(seg).encode())
    os.remove(path)
    return f'shipped {os.path.basename(path)} {len(payload)//1024}KB {seg["timerange"]}'

RETAIN_SECS = int(os.environ.get('RETAIN_SECS', 7200))   # keep 2h of history
_last_prune = 0

def prune():
    global _last_prune
    if time.time() - _last_prune < 300:
        return
    _last_prune = time.time()
    cutoff = int(time.time()) - RETAIN_SECS
    try:
        req('DELETE', f'{TAMS}/flows/{FLOW}/segments?timerange=[0:0_{cutoff}:0)', timeout=30)
        print(f'pruned store before {cutoff}', flush=True)
    except Exception as e:
        print(f'prune err: {e}', flush=True)

print('tams shipper up', flush=True)
while True:
    prune()
    now = time.time()
    files = sorted(glob.glob(f'{SPOOL}/seg-*.ts'))
    for p in files:
        if now - os.path.getmtime(p) < 2.5:      # still being written
            continue
        try:
            print(ship(p), flush=True)
        except Exception as e:
            print(f'ERR {os.path.basename(p)}: {e} (will retry)', flush=True)
            time.sleep(5)
            break
    # keep the spool bounded if TAMS is unreachable for long
    if len(files) > 600:
        for p in files[:len(files) - 600]:
            os.remove(p)
        print(f'spool overflow: dropped {len(files) - 600} oldest', flush=True)
    time.sleep(1)
