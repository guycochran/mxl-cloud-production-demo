# Resume / rebuild after teardown

The IBC-week VMs were deallocated. Everything needed to stand the facility
back up — anywhere — is in this repo. This is the warm-start map.

## What this repo already contains
- `scripts/quickstart.sh` + `docs/QUICKSTART.md` — fresh Ubuntu VM → cuttable,
  keyed, browser-watchable switcher in ~10 min. **Validated on a clean VM.**
  Start here.
- `bring-up-mxl.sh` — the whole production host (VM1) as an executable runbook:
  containers, ingests, layout, thumbs, grain probe, keyer, encoder, multiview,
  NMOS node. Adapt the IPs and run it.
- `tools/` — every pipeline program (see below).
- `docs/FINDINGS.md` — the failure taxonomy and every hard-won lesson (§12–17
  are the week's operational knowledge: reader-wedge species, the stabilizer,
  measurement doctrine, self-heal coordination, the runbook, standards).
- `docs/AWS-BUILD-PLAN.md` — two-VPC production build on EC2 (~$56 to first show).
- `docs/JONAS-FABRIC-HANDOFF.md` — receive our program as raw grains elsewhere.
- `docs/FIELD-NOTES.md` — the honest public writeup.
- `companion-module-mxl-switcher/` — Stream Deck panel (+ TURNKEY guide).
- `web/decks/` — the two presentation decks.

## Rebuild order (fastest to fullest)
1. **One box, prove it:** `scripts/quickstart.sh` on a fresh AVX-capable VM.
2. **Full production host:** adapt and run `bring-up-mxl.sh`. Deploy
   `flow_stabilizer.py` from the start (FINDINGS §13) — insulate every input
   before you have an audience, rather than retrofitting the reader-wedge
   workarounds under fire.
3. **Contribution host + fabric:** second VM, same AZ/rack, private IPs only
   (FINDINGS §7; `guest_ingest.py`, `guest-leg-doctor.sh`, fabric leg scripts).
   `iperf3` across the link first — confirm same-AZ before the 1.3 Gbps flow.
4. **TAMS:** `tams_shipper.py` → object store (native S3 on AWS). `docs/TAMS.md`.
5. **Standards + panel:** `tools/nmos_node.py` (+ a registry: nmos-cpp in
   Docker, or Buttons' built-in one) and the Companion module.

## Non-negotiables (or it won't start / will drift)
- **AVX CPU** — v210 conversion SIGILLs without it. Not burstable (t-class).
- **Size for the end state** — the full rig idles ~load 10 on 32 vCPU.
- **Fabric on same-AZ/rack private IPs** — cost *and* latency.
- **Grain probe + program-fps-doctor from day one** — rate metrics lie (§14).
- **Keep self-heal reflexes coordinated** — warm-up manual-only, no timed
  cascades, per-reflex cooldowns (§15).

## The tools, one line each
- `cam_ingest.py` / `cam2_ingest.py` — RTSP/SRT camera → MXL, offset-locked restamp.
- `cam_relay.py` — re-time a camera flow so the keyer reads the selector.
- `guest_ingest.py` — contributor SRT → conform 1080p30 → MXL; self-announce + escalation.
- `guest_audio.py` — guest RTSP audio → MXL audio flow.
- `layout_pgm.py` — 2-up/PiP/4-up compositor (grid-stamped output, storm-broken wedge-watch).
- `audio_pgm.py` — program audio mixer (per-input level/mute, commentary voice).
- `mxl_thumbs.py` — per-slot preview thumbs + health.json; inode-recreate self-heal.
- `grain_probe.py` — content-uniqueness probe across all flows (the wedge-catcher).
- `flow_stabilizer.py` — the reader-wedge structural fix (FINDINGS §13).
- `mxl_multiview.py` / `mv_encode.py` — compose-in-domain multiview, encode once.
- `tams_shipper.py` — 1 s program segments + storyboard sprites → object store.
- `guest-leg-doctor.sh` / `program-fps-doctor.sh` / `selector-doctor.sh` — watchdogs.
- `nmos_node.py` — IS-04 node, BCP-007-03 senders/receivers.
- `patch-target-ip.py` / `start-jonas-leg.sh` — cross-network fabric (TargetInfo sockaddr).

## If MXL itself has moved on
Much of `tools/` and half of FINDINGS exists to work around the v1.1.0
reader-lifecycle bug (§12). If a later MXL release fixes reader survival
across flow recreation, the stabilizer, the doctors, the warm-up, and the
repair cascade can largely retire — check that first; it changes everything
downstream.

---
*Built IBC week 2026 on the EBU MXL SDK. It was a good build.*
