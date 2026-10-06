# Guest-ingest: what to test when the VMs come back up

> **HW RUN — Oct 6 2026 (cold-clone on mxl-lab):**
> - **Test 1 (QR %3A fix): PASS** — a real iPhone published to `guest1` through the
>   literal-colon QR (`is publishing to path 'guest1'`, 2 tracks, no `invalid stream ID`).
> - **Test 3 (`--down` domain wipe): PASS** — `/dev/shm/mxl/domain_1` gone after teardown.
> - **Test 2 (cuttable + to program): PASS, but exposed a real bug + a redesign.**
>   The mediamtx **read-back** SRT hop restart-loops with `srtsrc … reason error (-5)`
>   on jittery sources. Root cause (isolated on HW): the `_restamp` probe's unclamped
>   ~1s resync writes a **backwards** PTS → non-monotonic into v210/mxlsink → `-5`.
>   **Fix shipped on branch `fix/srt-direct-listener-ingest` / draft PR #32:** (a) a
>   new **SRT-direct-LISTENER** ingest (`MXL_GUEST_TRANSPORT=srt-listen`) that drops
>   mediamtx from the contribution path entirely, and (b) a monotonic guard in
>   `_restamp`. Verified with a **live 1080p camera**: 0 `-5`, 0 restarts, flow latched
>   to selector idx 2, **cut to program** (frame showed the camera's real-time burn-in).
>   The camera was used as the continuous controlled source (phone kept dropping).

The phone→air guest path was proven end-to-end on Oct 5 2026 (a real iPhone via
Larix went to program). Three fixes landed offline afterward that need a live VM
to confirm. Run these in order; each has a clear pass/fail.

## Setup (once)
Any AVX-capable x86 cloud VM (Ubuntu 22.04/24.04) works — start yours however you
normally do (for the lab: `az vm start -g <resource-group> -n <vm-name>`; the real
values live in a private ops note, not this repo). Then on the VM:
```bash
git clone https://github.com/guycochran/mxl-switcher /tmp/hwtest && cd /tmp/hwtest
sudo scripts/quickstart.sh            # installs deps, starts the stack
```
Watch mediamtx live in a second terminal — this is the ONLY reliable signal, never
trust ffmpeg exit codes:
```bash
sudo docker logs -f mediamtx 2>&1 | grep -vE "172.17.|no stream is available"
```

## Test 1 — QR works (the %3A fix, commit d2818da)
- Open the control UI (`ssh -L 3100:127.0.0.1:3100 …`, then `http://localhost:3100`),
  click **Add your camera**, scan the Guest 1 QR with Larix, hit stream.
- **PASS:** mediamtx logs `is publishing to path 'guest1'` + `2 tracks (H264, MPEG-4 Audio)`.
- **FAIL signature to watch for:** `invalid stream ID 'publish%3Aguest1'` → the QR
  regressed to encoding the colon. The streamid must carry a LITERAL colon.

## Test 2 — guest becomes cuttable + goes to program (the real end-to-end)
With the phone streaming (Test 1 passing), leave it running 20–30s:
```bash
# does the guest flow exist + is it a selector input?
sudo docker logs --tail 5 guest1           # should NOT loop "not-negotiated"/"writer could not be created"
curl -s localhost:9604/pipeline/status | python3 -c "import sys,json;print(json.load(sys.stdin)['input_flow_uuids'])"
```
- **PASS:** guest1's flow (`9e111e00-…`) is in the selector inputs; cut to it
  (`curl -X POST -d '{"slot":<idx>}' localhost:9604/pipeline/active-input`) and grab
  the program frame — it shows the phone (the Larix free-tier watermark is the giveaway).
- This is the behaviour the writer-release fix (commit 22f7999) protects. If it loops
  `mxlsink: the UUID belongs to a flow with another active writer`, check there's only
  ONE guest_ingest per flow (`sudo docker exec guest1 pgrep -f guest_ingest` = the
  container's single loop; do NOT run manual guest_ingest alongside it — that was the
  self-inflicted cause of the Oct 5 loop).

## Test 3 — --down leaves a clean domain (commit f6e0579)
```bash
sudo scripts/quickstart.sh --down
sudo ls /dev/shm/mxl/ 2>/dev/null        # PASS: gone (no domain_1 with stale .mxl-flow dirs)
sudo scripts/quickstart.sh               # comes back clean, guest re-ingest works first try
```
- **PASS:** after --down, `/dev/shm/mxl` is gone; a fresh `up` + phone-stream attaches
  with no "another active writer" error (previously a stale flow survived --down and
  blocked the next run).

## Still a DESIGN DECISION, not yet coded (see below)
**Late-joining guest shows as a cuttable UI button.** Today, if a guest connects AFTER
boot, it's a live selector input (Test 2 confirms it's cuttable via the raw selector)
but it does NOT appear as a button in the control UI — the backend's slot list is
frozen at generation time. Two ways to fix, pick one before coding:
- **(A)** Seed guest slots (fixed UUIDs 9e111e00/9e222e00) in the generated manifest
  always, showing `live:false` until the guest connects. Simple; but review R2's test
  asserts "only existing sources" — that test would change.
- **(B)** Backend `/api/mxl/slots` + `/status` dynamically append any flow wired into
  the selector that isn't already a slot. Keeps R2's static manifest; bigger backend
  change; needs this HW session to validate the mapping.

## Teardown
```bash
az vm deallocate -g <resource-group> -n <vm-name>    # stop billing
```
