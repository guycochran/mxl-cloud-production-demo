# Adoption Gaps — cold-clone findings + roadmap

**What this is:** the output of an *adoptability* test — can someone who isn't Guy clone this
repo and get a cuttable MXL source, fast, with minimal MXL knowledge? (Strategy: features are
frozen; the objective is adoption-hardening. See the four-lane plan in the repo notes.)

**Test method:** fresh GCP `n2-standard-8` (never saw the repo), cold `git clone`, run the
README's documented `sudo scripts/quickstart.sh`, time it, drive it, log every wall, delete.

---

## Headline result (2026-10-02, GCP us-west1-b, deleted after ~5 min)

| Step | Measured |
|---|---|
| `git clone` (public repo, HEAD e86a9aa) | **2s** |
| `sudo scripts/quickstart.sh` → **ON AIR** | **153s (2m33s), exit 0** |
| WebRTC program page `:8889/mxl2webrtc/` | HTTP 200 |
| Real cut clip↔pattern (`/pipeline/active-input`) | both HTTP 200, selector running, 1080p30 |

**The README promises "~10 minutes." Reality on a fresh cloud VM: under 3 minutes to a
cuttable, keyed, browser-watchable switcher.** The claim is honest and *conservative* — the
product under-promises. ✅ The single-VM quickstart path genuinely works for a stranger today.

### What worked unaided (no wall)
- **AVX-512 present** (pinned `--min-cpu-platform="Intel Cascade Lake"` on GCP; QUICKSTART's
  `grep -m1 avx /proc/cpuinfo` preflight is the right gate — libmxl SIGILLs without it).
- **docker auto-installs** — quickstart's preflight installs it; a bare Ubuntu box is fine.
- **Flow UUIDs discovered at runtime** (`mxl-info`) — no hardcoded IDs to edit. Good design.
- **Idempotent + documented teardown** (`--down`). Self-contained: no prodbots backend, no
  tunnel, no tokens needed for the baseline.

### Nits (cheap doc fixes, not blockers)
- README/QUICKSTART timing says "~10 min" — update to the honest measured **~3 min** (website
  lane; published copy).
- QUICKSTART's `mxl-info -d /mxl-domain -l` flow-list example: confirm the exact output format
  it prints (a casual grep for "flow" matched 0 lines though the selector clearly lists two
  input_flow_uuids — likely the listing uses a different word). Verify the documented command.

---

## The real gap: "get MY video in" is not in the one-command tier

The quickstart gets you a cuttable **test pattern + file clip**. The moment a newcomer wants
*their own* feed, the easy on-ramp (phone → on air) lives only in the **full facility** path
(`scripts/bring-up-mxl.sh` + the Express backend + `/api/mxl/repair`), which assumes Guy's
infrastructure (the `hls2mxl` container name, `prodbots.com` URLs, the cochran.cloud tunnel,
API tokens, studio camera IPs). A stranger can't run that cold.

### ⭐ Recommended next step (Guy's idea, and it's the right one): SRT guest slots in the quickstart
**The fastest possible "first feed in" for a newcomer is a phone, not a camera:** scan a Larix
Broadcaster QR → the app opens pre-configured → tap → you're a cuttable switcher button. We
already built this for the live demo; the seam refactor turned it into a clean
`SrtGuestAdapter` (SRT → conform → restamp → slot → announce). The work is to bring it **down
into the quickstart tier**, decoupled from the prodbots backend:

1. Quickstart starts **mediamtx with SRT ingest enabled** (port 8890) + two guest selector
   slots (Guest 1 / Guest 2) wired to `SrtGuestAdapter`, using `contribution_core` directly.
2. Replace the backend `/api/mxl/repair` announce coupling with a **local, backend-free**
   re-attach (the quickstart already drives the selector directly via `:9604` — the guest
   adapter can self-announce against that, no prodbots).
3. Print a **Larix-ready SRT URL + a QR** in the final banner:
   `srt://<vm-ip>:8890?streamid=publish:guest1`. Newcomer installs the free Larix Broadcaster
   app, scans, taps — on air. (OBS/vMix/ffmpeg users get the same URL as text.)
4. Document the per-path latency reality (FINDINGS §9): ~1000 ms for a cellular phone, not the
   20 ms used for a wired camera — set the guest jitterbuffer accordingly.

Why this is the right #1: it's the **"somebody else gets THEIR content on screen in 5 minutes"**
moment — the thing that turns an impressive demo into something a stranger feels ownership of.
It needs no extra hardware, no RTSP, no config file. It also exercises the SourceAdapter seam
end-to-end on the adoption path, which is lane 1 + lane 2 at once.

### Other gaps (lower priority)
- The reviewer's **"choose a source" menu** (`setup.sh`: [1] pattern [2] RTSP [3] SRT guest
  [4] native MXL) becomes trivial once #1 lands — it's just a front-door over adapters that
  already exist.
- `bring-up-mxl.sh` cold-clone portability (de-prodbots-ify it) is a bigger lift; defer until
  the quickstart+guest tier is the proven on-ramp.

---

## Cost / honesty note
Test VM `mxl-adopt-test` (n2-standard-8, us-west1-b) lived ~5 min, deleted. The two 9/15
portability VMs (`mxl-gcp-1/2`, us-west1-a) were left TERMINATED, untouched. First real use of
the $300 GCP credit (expires 2026-12-02); spend was a few cents. `us-west1-a` had a transient
capacity shortfall → created in `-b` (fine for a single VM; same-zone-only matters only for the
multi-VM fabric's cross-zone egress). Timing log: `~/Projects/adoption-timing.log`.
