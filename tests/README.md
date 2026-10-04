# Tests

Fast, hardware-free guards for the hard-won media core. They run in CI (`.github/workflows/ci.yml`) and locally with:

```bash
pip install pytest
python -m pytest        # from the repo root
```

**No GStreamer, no Docker, no MXL needed.** `conftest.py` installs a stub `gi`
(GStreamer binding) so the contribution seam imports and builds its launch strings
without a real pipeline. The tests assert on *what pipeline the seam constructs* and
on the pure branching logic — which is exactly where a well-meaning refactor silently
breaks the media path.

| File | Protects |
|---|---|
| `test_contribution_core.py` | Cadence constants (2-grain margin, sustained-drift re-sync), the conform-vs-native branch, the **one-videorate** rule (a past double-videorate bug), `mxlsrc video-flow-id` vs `mxlsink flow-id`, the orthogonal `needs_conform`/`timing_policy` properties, the `zoomiso_adapters()` factory (both flow shapes), backend-free `repair_url` disabling. |
| `test_launch_parity.py` | Golden-string pin of the full guest SRT launch pipeline — the "byte-identical after the seam refactor" guarantee, enforced forever. |
| `test_docs_links.py` | Every repo-relative Markdown link resolves on disk. |
| `test_js_backend.py` + `js/*.test.js` | Backend control-route auth (`MXL_CONTROL_TOKEN`) + `/repair` rate limit (Node `--test`, no npm deps; skipped without `node`). |
| `test_no_hardcoded_hosts.py` | Tripwire: no NEW IP literals / site hostnames outside a per-file allowlist (ratchet — stale entries fail too). |
| `test_env_config.py` | Env-var overrides (facility loaders, bring-up block) keep the historical production defaults; every var is in `docs/CONFIG.md`. |
| `test_quickstart_hardening.py` | quickstart graphics-server bind/no-listing, optional guest SRT passphrase config, SRI-pinned CDN scripts. |

If a test fails, **do not just update the test** — re-read the cited FINDINGS section
first. These numbers and strings cost real debugging to discover.

The real end-to-end MXL/container smoke test (`sudo scripts/quickstart.sh` → ON AIR →
cut HTTP 200) needs AVX-512 hardware and runs separately — see the `integration-smoke`
job in the CI workflow and `docs/ADOPTION-GAPS.md`.
