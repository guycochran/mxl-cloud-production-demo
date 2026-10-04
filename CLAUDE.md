# CLAUDE.md — mxl-cloud-production-demo

Guidance for Claude Code working in this repo.

## 🤝 Active collaboration: code review in progress (Oct 2026)

An **independent reviewer** is auditing the MXL v1 switcher. We communicate **through
the repo** — there is no direct messaging between us.

**AT THE START of every session working in this repo:**
1. `git fetch origin` and read **`docs/REVIEW-RESPONSE.md`** (the shared review thread).
   The reviewer may open it on a branch / draft PR first — check `git branch -r` and
   `gh pr list` for a branch like `review/*` or a PR adding that file.
2. Read any open PRs' descriptions + comments (`gh pr view <n>`).

**AT THE END of every session:** update `docs/REVIEW-RESPONSE.md` with your replies —
mark each issue `open` / `fixed` / `disputed` / `needs-hw-verify`, reference the commit
or PR that addresses it, and commit. Keep the thread building.

**Rules of engagement (both sides):**
- Each issue is numbered and carries a status: `open`, `fixed`, `disputed`, `wontfix`,
  `needs-hw-verify`.
- Fixes land on a **branch + draft PR**, never directly on `master`.
- **Never touch the live system** from a review branch: no deploys, no tunnel edits, no
  starting/stopping the Azure VM, no restarting `mxl-switcher-ui.service`. Code + tests
  + docs only. HW verification is a separate, explicitly-authorized step.
- Disagree in writing in the file, with the code reference — don't silently override.

**Review state as of this writing:** reviewer found 5 confirmed issues (see
`docs/REVIEW-RESPONSE.md` once it lands, and the author's handoff in `REVIEW-HANDOFF.md`).
The big one: #2 — the UI drives the manifest's FIXED flow UUIDs, but `quickstart.sh`
DISCOVERS them at runtime, so a stranger's fresh install has mismatched IDs and every
cut returns "source not attached." Fix = generate the slot map from discovered IDs.

## Repo essentials
- **v1 switcher:** `web/local.html` (UI) + `backend/local-server.js` (host) +
  `backend/mxl-routes.js` (the open `/api/mxl/*` control routes). Self-contained, no CDN.
- **Tests:** `python3 -m pytest` (102) + `node --test tests/js/*.test.js` (12). Both must pass.
- **Air-gap rule:** `web/local.html` must load NOTHING from external hosts (fonts are
  self-hosted woff2 in `web/fonts/`). `tests/test_local_ui.py` enforces this.
- **Auth:** `MXL_CONTROL_TOKEN` (optional) gates all mutating routes; `GET /status` open.
- **Deployed:** a public URL via `mxl-switcher-ui.service` (user systemd, behind a
  cloudflared tunnel). The specific URL, VM IP, resource group, and tunnel UUID are
  deployment-specific and live in a private ops note, NOT this repo (Review R6e).
  DO NOT redeploy or touch the tunnel from a review branch.
- **Facility:** the lab Azure VM ⚠️ bills while up — deallocate when HW testing is
  done. HW truth-check for program-follows-cut: grab the keyer-PGM flow frame
  (`gst-launch mxlsrc video-flow-id=<keyer-uuid> ! jpegenc`) inside the ingest container.
