# MXL → TAMS: the full recipe

The DMF white paper notes *"MXL Grains can be grouped as TAMS Flow Segments"*
and leaves the rest open. This is the working implementation from our cloud
production: the live MXL program lands in a [TAMS](https://github.com/bbc/tams)
store with capture-derived timeranges, playable and clippable **while the show
is still being recorded**. Live at
[prodbots.com/mxl-tams.html](https://prodbots.com/mxl-tams.html).

Everything here is demo-grade by intent — small, readable, and honest about
its shortcuts — but the timing model and the store semantics are the real
thing, and the whole bridge is ~90 lines of Python plus one ffmpeg.

## Architecture

```
VM1 (production)          VM2 (fabric receiver)                VM3 (TAMS store)
────────────────          ─────────────────────                ────────────────
camera → selector →       Keyer PGM flow arrives               tams-gateway :8000
keyer → "Keyer PGM"  ──►  via MXL Fabrics into                 (Eyevinn, built from
flow in /dev/shm     TCP  /dev/shm/mxl/domain_fabric           source — no published
                          │                                    image)
                          ├─ mxl2webrtc (stock container)      MinIO :9000  (media +
                          │    └─ mediamtx rtsp :8554          thumbs, bucket
                          │                                    "tams-media")
                          ├─ ffmpeg segmenter                  CouchDB      (segment
                          │    1 s MPEG-TS → /srv/tams-spool   / flow metadata)
                          │    (epoch-stamped filenames)
                          └─ tams_shipper.py ────────────────► storage / PUT / segments
                                                               ▲
                          browser scrub page ──────────────────┘
                          (HLS timerange playback + thumbs)
```

All hosts are NTP-synced. That single fact is what makes the rest work: the
segmenter stamps wall-clock epochs into filenames, the shipper turns them into
TAMS timeranges, and the in-picture cloud-keyed clock matches the timerange it
plays back from — we verified a frame pulled from 11½ minutes in the past to
the second.

## The mapping

| MXL | TAMS |
|---|---|
| Flow (Keyer PGM, v210 @ 30 grains/s) | Flow `7a350001-…` (H.264 in TS) |
| 30 grains | one 1-second Flow Segment |
| grain index / TAI time | segment timerange `[epoch:0_epoch+1:0)` |
| — | clips = **new flows** re-registering the *same* media objects under the clip's timerange (zero bytes copied; the store refcounts, so deleting a clip never touches the archive) |

The H.264 detour (grains → encoder → TS → store) is a pragmatic shortcut. The
white-paper-literal version — a grain-native segmenter grouping v210 grains
into Flow Segments with no transcode — is the natural next step and the code
paths here don't preclude it.

## Bring-up

**VM3 — the store** (three containers):
- [Eyevinn tams-gateway](https://github.com/Eyevinn/tams-gateway) on :8000 —
  build from source, there is no published image.
- MinIO on :9000, bucket `tams-media`, plus a public-read `thumbs/` prefix.
- CouchDB for gateway metadata.
- Register one flow (UUID of your choosing) via the gateway API.

**VM2 — the bridge** (two processes, both dumb on purpose):

```sh
# segmenter: cut the program into 1 s epoch-named TS segments
while :; do
  ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
    -i rtsp://127.0.0.1:8554/mxl2webrtc -c copy \
    -f segment -segment_time 1 -segment_format mpegts \
    -reset_timestamps 1 -strftime 1 /srv/tams-spool/seg-%s.ts
  sleep 3
done
```

```sh
# shipper: register every segment in the store (see tools/tams_shipper.py)
TAMS_URL=http://<vm3>:8000 TAMS_FLOW=<flow-uuid> python3 tams_shipper.py
```

Per segment the shipper does exactly what the TAMS spec intends:
`POST /flows/{id}/storage` → gateway allocates an object id →
`PUT` the bytes to object storage → `POST /flows/{id}/segments` with
`{object_id, timerange}`. It also cuts one ~5 KB jpeg per second into
`thumbs/<epoch>.jpg` — that's the scrub bar's filmstrip.

**Playback** is the store's own HLS endpoint: ask for a timerange, get a
playlist. The scrub page is just hls.js pointed at timerange queries, with
mark-in/out building clip timeranges client-side. A 15 s clip of the live show
exports to MP4 in ~1.5 s — segment concat, no re-encode, because the clip
*already exists* as a timerange.

## Gotchas we earned (read before replicating)

1. **Ship media over the fastest path, not the pretty one.** The gateway's
   presigned URLs pointed at our public tunnel hostname; 750 KB/s of PUTs
   through a Cloudflare tunnel could not hold realtime. The shipper uploads
   directly to MinIO using the gateway-allocated object key and keeps the
   public URLs for browser playback only.
2. **The gateway ignores `?reverse=true` on `/segments`** (returns oldest
   first). Query a timerange near *now* instead — this one masqueraded as
   "the recording froze two hours ago" and cost us a debugging session.
3. **Registration order doesn't matter — exploit it.** Segments are
   timerange-keyed, so the shipper ships 4-wide in parallel to hold 1 s
   cadence. Skip files younger than ~2.5 s (still being written) and runt
   segments (<10 KB, startup artifacts).
4. **Prune both halves.** We retain 2 h: one `DELETE /flows/{id}/segments
   ?timerange=[0:0_cutoff:0)` plus an S3 sweep of expired `thumbs/`. Forgetting
   the second half leaks thumbnails forever.
5. **Bound the archive playlist you hand the player.** A full 2 h window is a
   ~3 MB m3u8 with ~7,200 entries — hls.js manages every fragment on every
   seek and turns to molasses. Default the UI to a 30 min window, full archive
   on explicit request.
6. **Thumbnails: nearest-available, never exact.** The newest ~10 s of thumbs
   lag and 1 s holes happen; an exact-epoch fetch means broken images at the
   live edge. Walk outward ±1..±4 s to the nearest existing frame.
7. **Cloudflare bans Python's default urllib User-Agent** (error 1010) —
   set any custom UA on gateway requests through a tunnel.
8. **Keep the spool bounded.** If the store is unreachable, drop oldest beyond
   ~600 segments; the show must not fill the disk.

## Honest limits

Single flow, no auth on the store (NSG/IP-locked instead), H.264 mezzanine
rather than grain-native segments, and the segmenter trusts the encoder's
30 fps cadence rather than reading grain indices. All fixable; none blocked
by TAMS or MXL themselves — which is rather the point.
