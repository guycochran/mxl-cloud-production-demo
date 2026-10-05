# For testers — fire it up in ~3 minutes

Hand-this-to-a-tester guide. Full detail is in [QUICKSTART.md](QUICKSTART.md);
this is the short path plus the three things that actually trip people up.

**Verified Oct 2026** on a fresh cloud VM: clone → `quickstart.sh` → cuttable,
browser-watchable switcher in **~2.5 min** (first run; faster after). Every step
below was run end-to-end on current `master`.

---

## 1. Get a machine that will work  ⚠️ read this first

The one hard requirement — if you skip it, it crashes instantly (`SIGILL`):

- **x86-64 CPU with AVX.** Any cloud x86 VM qualifies: **Azure D-series v5,
  AWS m5/m6i, GCP n2**. ~8 vCPU recommended.
- **Ubuntu 22.04 or 24.04.**
- **Apple Silicon / local VirtualBox/UTM:** only if the CPU is x86 with AVX
  passed through. On QEMU/KVM you **must** set the CPU model to `host`
  (otherwise no AVX → crash). A cloud VM is the path of least resistance.

Docker, ffmpeg, and python3 install themselves — a bare Ubuntu box is fine.

## 2. Open these ports in your cloud firewall  ⚠️ the #2 gotcha

The script can't open your cloud security group for you. If the program page
won't load, it's almost always this:

| Port | For |
|---|---|
| `8889/tcp` | WebRTC program page + signaling (watch the output) |
| `8189/udp` | WebRTC media |
| `8890/udp` | optional — only if you push your own camera/phone (step 5) |

## 3. Run it

```bash
git clone https://github.com/guycochran/mxl-switcher
cd mxl-switcher
sudo scripts/quickstart.sh
```

When it finishes it prints the cut commands and the watch URL. You get a test
pattern + a looping clip through a real MXL shared-memory domain, an HTML5
lower-third keyed over program, and WebRTC out. **No tokens, no accounts, no
external services.** The self-healer is **off by default** — it's a read-only
lab until you ask for more.

## 4. Drive it

- **Watch the program:** open `http://<your-vm-ip>:8889/mxl2webrtc/` in a browser.
- **Cut between sources** (the script prints these with the real values):
  ```bash
  curl -X POST -H 'Content-Type: application/json' -d '{"slot":1}' http://127.0.0.1:9604/pipeline/active-input  # clip
  curl -X POST -H 'Content-Type: application/json' -d '{"slot":0}' http://127.0.0.1:9604/pipeline/active-input  # pattern
  ```
- **Or drive it in a browser:** the control UI is on `http://127.0.0.1:3100/`
  (localhost-only; tunnel it with `ssh -L 3100:127.0.0.1:3100 <user>@<vm-ip>`).

## 5. Put *your own* video on air (optional)  ⚠️ the #3 gotcha

The quickstart gives you a pattern + clip. To add your own feed, use the two
**SRT guest slots** — a phone with the free **Larix Broadcaster** app is the
easy path:

- Scan the committed QR: [`web/qr-larix-guest1.png`](../web/qr-larix-guest1.png).
  **Use that QR** — don't hand-build one. Free Larix needs the `srtstreamid`
  parameter spelled exactly that way; the committed QR already encodes it.
  (Hand-rolled QRs with `streamid` or an in-URL id silently fail to publish.)
- Point the SRT host at your VM's public IP, open `8890/udp`, and the phone
  appears as **Guest 1**, cuttable like any other source.
- Any SRT or ffmpeg source works too — see [QUICKSTART.md](QUICKSTART.md) §guest.

## 6. Tear down

```bash
sudo scripts/quickstart.sh --down
```

Idempotent — safe to re-run the quickstart anytime. The shared-memory domain is
volatile (gone on reboot); just run the quickstart again.

---

## What testers should NOT run

`scripts/bring-up-mxl.sh` is the **production facility** path — it assumes
specific infrastructure (named containers, a backend URL, a tunnel, real camera
IPs) and won't come up cold on a tester's box. Stay on `quickstart.sh`; that's
the adopter-grade path and the one that's verified for a fresh install.

## If something's wrong

| Symptom | Almost always |
|---|---|
| Crashes immediately on start | No AVX (step 1) — use a cloud x86 VM or set QEMU `-cpu host` |
| Program page won't load in browser | Firewall ports not open (step 2) |
| Phone won't go on air | Wrong Larix QR — use the committed one (step 5) |
| A cut returns "source not attached" | Shouldn't happen on current `master` (fixed). Re-run `quickstart.sh` — the domain may have been reset by a reboot. |
