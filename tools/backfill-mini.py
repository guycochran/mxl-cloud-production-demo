#!/usr/bin/env python3
# One-time: build ~/tams-mini/<m>.jpg from the last-12h sprite sheets already in
# MinIO, so overview_pass produces a full 12h strip immediately (instead of
# filling 1 tile/min over 12h). Additive; reads sprites/, writes local minis.
import os, time, subprocess, boto3
_pw=[l.split('=',1)[1].strip() for l in open(os.path.expanduser('~/.tams-s3.env')) if l.startswith('S3PW=')][0]
S3=boto3.client('s3',endpoint_url='http://20.112.83.140:9000',aws_access_key_id='tams',
                aws_secret_access_key=_pw,region_name='us-east-1')
MINI=os.path.expanduser('~/tams-mini'); os.makedirs(MINI,exist_ok=True)
TW,TH=96,54
cutoff=(int(time.time())-12*3600)//60*60
# list sprite objects (paginate)
tok=None; keys=[]
while True:
    kw={'Bucket':'tams-media','Prefix':'sprites/','MaxKeys':1000}
    if tok: kw['ContinuationToken']=tok
    r=S3.list_objects_v2(**kw)
    for o in r.get('Contents',[]):
        k=o['Key']; base=k.split('/')[-1]
        if base[:1].isdigit() and base.endswith('.jpg'):
            m=int(base.split('.')[0])
            if m>=cutoff: keys.append(m)
    if r.get('IsTruncated'): tok=r['NextContinuationToken']
    else: break
keys=sorted(set(keys)); done=0; skip=0
for m in keys:
    dst=f'{MINI}/{m}.jpg'
    if os.path.exists(dst): skip+=1; continue
    tmp=f'{MINI}/.src-{m}.jpg'
    try:
        S3.download_file('tams-media',f'sprites/{m}.jpg',tmp)
        subprocess.run(['ffmpeg','-y','-loglevel','error','-i',tmp,
                        '-vf',f'crop=384:216:0:0,scale={TW}:{TH}','-q:v','6',dst],
                       timeout=15,check=True)
        done+=1
    except Exception as e:
        print(f'{m}: {e}')
    finally:
        try: os.remove(tmp)
        except OSError: pass
print(f'backfill done: {done} built, {skip} already present, {len(keys)} sprites in 12h window')
