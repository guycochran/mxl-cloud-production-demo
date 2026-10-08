<!-- SPDX-License-Identifier: Apache-2.0 -->
# Contribution ‖ Mixer: cameras, and isolating the attack surface

**The question this answers:** how does a new user get *their* cameras in (SRT or MXL),
and — once strangers can push feeds — how do you keep a hacked or overwhelmed contribution
ingest from taking down the program?

**Short answer:** the contribution edge is a *replaceable media function* behind a
`SourceAdapter` (see [CONTRIBUTION-SEAM.md](CONTRIBUTION-SEAM.md)). Adding a camera is adding
an adapter. Isolating contribution from the mixer is a *placement* decision — the same
adapters, moved onto their own host with the MXL fabric as the trust boundary. You scale the
isolation to the threat, in three tiers.

---

## Cameras: SRT, RTSP, or native MXL — all the same seam

A camera is just another contribution source. The adapters already exist
([tools/adapters.py](../tools/adapters.py)); surfacing a camera slot is wiring, not new code.

| Your camera speaks | Adapter | How the user points it in |
|---|---|---|
| **SRT** (Makito, OBS, vMix, hardware encoders) | `SrtGuestAdapter` | push to `srt://<host>:8890?streamid=publish:cam1` — identical to the guest path, just a `cam1`/`cam2` stream name. **This is the cam1/cam2-via-SRT workflow.** |
| **RTSP** (IP/PTZ cameras) | `RtspCamAdapter` | give the quickstart `rtsp://user:pass@<camera-ip>/path`; it pulls + conforms + restamps. |
| **native MXL** (an MXL-emitting camera/gateway) | `ZoomIsoMxlAdapter`-style (needs_conform=False) | the camera writes v210 grains into the domain directly — no decode, no conform. Finish the stub against a real native source. |

So "cameras via MXL or optionally SRT" = exposing `cam1`/`cam2` slots the same way the quickstart
now exposes `guest1`/`guest2`. SRT is the easy default (any encoder); RTSP for IP cameras;
native MXL for the zero-transcode future.

---

## The isolation tiers — scale to the threat

### Tier 1 — single box (the quickstart default)
Contribution ingests and the mixer share one host; each ingest is its **own container**, the
program chain (selector/keyer/encoder) is other containers. Right for: a newcomer, a dev box, a
demo, a trusted LAN. Cold-clone to on-air in ~3 min. **This is what `quickstart.sh` gives you.**

Limit: a hostile or malformed contribution *can* contend for the host's CPU/memory with the
program. For trusted inputs that's fine; for strangers, harden or split.

### Tier 2 — single box, container-hardened (cheap hardening, no 2nd host)
Still one VM, but treat each guest/camera container as untrusted:
- `--memory` / `--cpus` limits so one flooded ingest can't starve the keyer (the CEF keyer needs
  protected headroom or the program freezes first — FINDINGS §9).
- `--read-only` rootfs, drop caps, no network path to the selector/keyer control ports.
- The ingest can only *write grains into the domain*; it cannot touch the program pipeline's APIs.
A crashed or hostile guest then takes down **its own container**, not the show. ~80% of the
isolation for zero extra hosts. (Not yet wired into quickstart — a good next hardening pass.)

### Tier 3 — two hosts: Contribution DMZ ‖ Mixer (the production answer)
**This is the right shape once strangers can push feeds, and it's exactly your instinct.** The
contribution host is the attack surface: world-open SRT, untrusted inbound media, decoders
parsing hostile bytes. Put it on its **own host/VPC**; the mixer consumes only **clean, conformed
v210 grains over the MXL fabric** — never the raw stranger stream. The fabric boundary is the
trust boundary.

```
  CONTRIBUTION host (DMZ)                      MIXER host (program)
  ┌─────────────────────────┐   MXL fabric    ┌───────────────────────────┐
  │ world-open SRT :8890     │  (tcp, only the │ fabric target ── SELECTOR │
  │ guest/cam ingests ───────┼──2 ports peered─┼─▶ v210 grains → keyer →   │
  │ (decode hostile bytes    │   explicitly)   │   encoder → WebRTC/SRT out │
  │  HERE, in the blast zone)│                 │ NO inbound from internet   │
  └─────────────────────────┘                 └───────────────────────────┘
   compromised/flooded here  ──X──  has no route to the program except grains
```

A compromised or misbehaving contribution host has **no route** to the program chain except the
fabric ports you explicitly peer — the broadcast ingest-DMZ pattern in cloud vocabulary. The
live Azure demo already ran exactly this (VM2 contribution → fabric → VM1 production); the full
cloud layout — two VPCs, peering, the same-AZ cost rule, instance sizing — is specified in
**[AWS-BUILD-PLAN.md §3](AWS-BUILD-PLAN.md)**. Nothing new to invent: it's the quickstart's
adapters, placed on two hosts.

---

## Picking a tier

- **Trusted inputs only** (your own cameras on your LAN) → **Tier 1**. Don't over-build.
- **Strangers push feeds, one box is fine** (small show, cost-sensitive) → **Tier 2** (harden the
  containers). Good default for a public-facing single VM.
- **Strangers + the program must never go down** (a real live show) → **Tier 3** (split hosts).

The seam makes moving between tiers a *placement* change, not a rewrite — the same
`SrtGuestAdapter`/`RtspCamAdapter` run whether they're next to the mixer or a fabric hop away.

*Status: Tier 1 shipped (quickstart, guest + camera slots). Tier 2 = documented, not yet wired.
Tier 3 = proven on the live demo + fully specified in AWS-BUILD-PLAN §3; not in the one-command
quickstart by design.*
