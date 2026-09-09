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
import concurrent.futures
import glob
import json
import os
import subprocess
import time
import urllib.request

TAMS = os.environ.get('TAMS_URL', 'http://20.112.83.140:8000')
FLOW = os.environ.get('TAMS_FLOW', '7a350001-aaaa-4bbb-8ccc-000000000001')
SPOOL = os.environ.get('SPOOL', '/srv/tams-spool')
SEG_SECS = 1

# Upload media DIRECTLY to MinIO (VM2->VM3:9000, NSG-allowed, LAN-fast) using the
# gateway-allocated object key; the gateway's presigned public-host URLs are for
# browser playback only — routing 750KB/s of PUTs through the Cloudflare tunnel
# could not keep realtime.
import boto3
_s3pw = [l.split('=', 1)[1].strip() for l in open(os.path.expanduser('~/.tams-s3.env')) if l.startswith('S3PW=')][0]
S3 = boto3.client('s3', endpoint_url='http://20.112.83.140:9000',
                  aws_access_key_id='tams', aws_secret_access_key=_s3pw,
                  region_name='us-east-1')

def req(method, url, data=None, ctype='application/json', timeout=15):
    r = urllib.request.Request(url, data=data, method=method)
    # Cloudflare bans the default Python-urllib UA (error 1010) on this zone
    r.add_header('User-Agent', 'mxl-tams-bridge/1.0')
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
    bucket, key = obj['object_id'].split('/', 1)
    S3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType='video/mp2t')
    # scrub-bar thumbnail: one ~5KB jpeg per second, public-read prefix
    try:
        th = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', path,
                             '-frames:v', '1', '-vf', 'scale=192:-1', '-q:v', '8',
                             '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
                            capture_output=True, timeout=10).stdout
        if th:
            S3.put_object(Bucket=bucket, Key=f'thumbs/{epoch}.jpg', Body=th,
                          ContentType='image/jpeg', CacheControl='public, max-age=86400')
    except Exception:
        pass
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
    try:  # expire old thumbnails too
        pages = S3.get_paginator('list_objects_v2').paginate(Bucket='tams-media', Prefix='thumbs/')
        old = [{'Key': o['Key']} for pg in pages for o in pg.get('Contents', [])
               if int(o['Key'].split('/')[1].split('.')[0]) < cutoff]
        for i in range(0, len(old), 1000):
            S3.delete_objects(Bucket='tams-media', Delete={'Objects': old[i:i+1000]})
        if old:
            print(f'pruned {len(old)} thumbs', flush=True)
    except Exception as e:
        print(f'thumb prune err: {e}', flush=True)

POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)

print('tams shipper up (parallel x4)', flush=True)
while True:
    prune()
    now = time.time()
    files = [p for p in sorted(glob.glob(f'{SPOOL}/seg-*.ts'))
             if now - os.path.getmtime(p) >= 2.5]           # skip in-progress file
    # segments are timerange-keyed, so registration order doesn't matter —
    # ship in parallel to keep up with 1s cadence through the tunnel
    for p, fut in [(p, POOL.submit(ship, p)) for p in files[:12]]:
        try:
            print(fut.result(), flush=True)
        except Exception as e:
            print(f'ERR {os.path.basename(p)}: {e} (will retry)', flush=True)
    # keep the spool bounded if TAMS is unreachable for long
    if len(files) > 600:
        for p in files[:len(files) - 600]:
            os.remove(p)
        print(f'spool overflow: dropped {len(files) - 600} oldest', flush=True)
    time.sleep(1)
