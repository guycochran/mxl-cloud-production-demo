# Facility-identifier redaction — note to the reviewer

**Re:** review items **R6d** and **R6e** in `docs/REVIEW-RESPONSE.md`.

Thanks for catching these — you were right. Real facility identifiers had made
it into this (now public) repo from the original build commits back in September,
not from anything in the review branches. We're scrubbing them, and R6d/R6e are
the reason. Status: **fixed** (this PR + PRs #26/#27).

## What was exposed and is now a placeholder

All replaced with RFC 5737 documentation-range placeholders (`203.0.113.0/24`),
or made a required env var with no default:

| Identifier | Was | Now |
|---|---|---|
| MXL control-plane VM public IP | `20.64.205.144` | `MXL_VM_IP` — **required**, no default (`bring-up-mxl.sh`); `<VM_PUBLIC_IP>` placeholder in docs/manifest |
| Guest SRT VM public IP | `20.230.168.32` | `203.0.113.32` (`web/mxl.html`, 4 spots) |
| TAMS / MinIO box public IP | `20.112.83.140` | `203.0.113.140` (`tools/tams_shipper.py`, `tools/backfill-mini.py`, default behind `TAMS_HOST`) |
| Site NSG-allowed source IP | `50.106.4.50` | `203.0.113.50` (default behind `MXL_SITE_IP`) |
| Azure resource group | `ohg-mxl-lab` | `MXL_AZ_RESOURCE_GROUP` — **required**, no default |
| Azure VM name | `mxl-lab` | `MXL_AZ_VM_NAME` — **required**, no default |

Private/LAN addresses (`10.0.0.x` VNet, `192.168.8.177` Makito, `172.17.0.1`
docker bridge) are left as-is — not internet-routable, not sensitive.

The `tests/test_no_hardcoded_hosts.py` tripwire allowlist was updated to match
the placeholders, so it still fails on any *new* real IP added outside the
allowlist. Full suite green: 104 pytest + 12 node.

## Still open (your R6e also mentioned it)

- **History scrub:** the real IPs remain recoverable from pre-redaction commits
  in git history. The VMs are deallocated (the static IPs are reserved but
  nothing listens), so this is low-risk, but a `git filter-repo` pass is the
  clean finish if we decide the identifiers warrant it.

If you spot any other site-specific identifiers in future review branches,
please flag them the way you did R6d/R6e.
