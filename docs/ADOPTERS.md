# Adopters — clone the core, build your skin

**Audience:** teams who want a working MXL lab today and an organisation-specific
control surface tomorrow. Written for early adopters in the DMF / MXL community —
notably **EBU** and **CBC Radio** — and anyone else evaluating this repo as a
starting point.

This note is the short pitch. The boundary design lives in
[ARCHITECTURE-CORE-AND-SKINS.md](ARCHITECTURE-CORE-AND-SKINS.md); the versioned
HTTP surface is sketched in [V1-CONTRACT.md](V1-CONTRACT.md).

---

## The story in four steps

1. **Clone and run the open core** on a commodity VM (Ubuntu x86-64 with AVX).
   One script brings up a cuttable, keyed, browser-watchable MXL switcher:

   ```bash
   git clone https://github.com/guycochran/mxl-switcher
   cd mxl-switcher
   sudo scripts/quickstart.sh
   ```

   Details and measured cold-clone times: [QUICKSTART.md](QUICKSTART.md).

2. **Validate with `mxl-doctor`.** The default report is read-only and
   backend-free — containers, flow presence, program path. Add `--deep` for
   unique-frame liveness:

   ```bash
   sudo scripts/mxl-doctor
   sudo scripts/mxl-doctor --deep
   ```

3. **Treat the core as a generic DMF lab.** Sources, slots, hard cut, key,
   program video, and health stay organisation-neutral. No customer workflow,
   branding, or facility-specific topology belongs in this repo.

4. **Build a skin against `/api/mxl/v1`.** Your UI (and any bridge from an
   outside system) talks only to the versioned HTTP API — not to media-function
   ports, not to docker, not to the MXL domain directly. Org-specific workflows
   live in the skin / downstream project. See the architecture note for the
   contract rules.

```
   ┌─────────────────────────────┐
   │  your skin / bridge         │  ← org workflows, branding, queues
   │  (calls /api/mxl/v1 only)   │
   └──────────────┬──────────────┘
                  │
   ┌──────────────▼──────────────┐
   │  open MXL switcher core     │  ← this repo (generic DMF lab)
   │  quickstart · doctor · API  │
   └─────────────────────────────┘
```

---

## Who this is aimed at

| Adopter | Why point here |
|---|---|
| **EBU** | A public, runnable reference that exercises the [MXL SDK](https://github.com/dmf-mxl/dmf-mxl) end-to-end on commodity cloud/COTS — useful as a shared on-ramp for DMF experiments. |
| **CBC Radio** | Same core that already runs on the open [cbcrc/mxl-hands-on](https://github.com/cbcrc/mxl-hands-on) media-function containers; a place to stand up a lab quickly, then layer a Radio-specific skin. |
| **Anyone else** | Clone → quickstart → doctor → skin. The core stays generic; your product shape is a skin. |

Not affiliated with EBU or CBC; this repo is an independent lab offering.

---

## Pinning the core

Downstream projects should **pin a core release tag**, not float on `master`:

- Tags look like `core-vX.Y.Z` (example on the repo today: `core-v1.0.0`).
- **Recommend waiting for / pinning `core-v1.0.1`** once it is tagged (pending),
  then bump deliberately when you take fixes.
- Update flow: fix lands in this repo → tagged release → your project bumps the
  pin in one small PR and re-runs your own tests against the new tag.

Until a tag you trust exists, treat `master` as moving and expect contract
notes in [V1-CONTRACT.md](V1-CONTRACT.md) / release notes.

---

## Defaults that matter for adopters

| Concern | Default in this repo | How to change |
|---|---|---|
| **Desired-state** | **Report-only** — compares desired vs actual; does not auto-repair. See [DESIRED-STATE-v0.md](DESIRED-STATE-v0.md). | Optional repair is a later, gated phase — not on by default. |
| **Self-healer** | **Opt-in / off** — quickstart does not start `tools/mxl-selfheal.sh` unless you set `MXL_SELFHEAL=1`. | `sudo MXL_SELFHEAL=1 scripts/quickstart.sh` (see [CONFIG.md](CONFIG.md)). |
| **Control auth** | Open locally unless you set a token. | `MXL_CONTROL_TOKEN` / `MXL_CONTROL_REQUIRE_TOKEN` in [CONFIG.md](CONFIG.md) / [SECURITY.md](../SECURITY.md). |
| **Container images** | Pinned by digest for reproducibility. | `MXL_BLEEDING_EDGE=1` only when deliberately testing upstream `:latest` ([VERSIONS.md](VERSIONS.md)). |

---

## What stays out of the open core

- Organisation-specific UIs, queues, and branding → **skins / downstream**.
- Deployment specifics (domains, tunnels, tokens, VM sizes, schedules) → **downstream**.
- Private facility runbooks and internal product wiring → **not in this repo**.

If a newcomer on a fresh VM would not need it for the quickstart path, it is
not core. When in doubt, open an issue or a draft PR against this repository
rather than carrying a long-lived fork patch.

---

## Next reading

1. [QUICKSTART.md](QUICKSTART.md) — get on air.
2. [ARCHITECTURE-CORE-AND-SKINS.md](ARCHITECTURE-CORE-AND-SKINS.md) — bones vs skins.
3. [V1-CONTRACT.md](V1-CONTRACT.md) — `/api/mxl/v1` shape.
4. [DESIRED-STATE-v0.md](DESIRED-STATE-v0.md) — report-only drift model.
5. [FINDINGS.md](FINDINGS.md) — field lessons before you debug.
