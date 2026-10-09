"""Tripwire: no NEW hardcoded IPs / site hostnames in code.

Site-specific endpoints (VM IPs, the TAMS/MinIO host, the facility backend URL, the
public feed tunnel, ...) are configured through env vars whose defaults are the current
production values (see docs/CONFIG.md). Those defaults — and a handful of comments and
protocol constants — are allowlisted below, per file. If you add an IP literal or a site
hostname anywhere else, this test fails: route it through an env var (with the old value
as the default) and, if the default itself must live in code, add it to ALLOW with a reason.

The allowlist is a ratchet: an entry that no longer occurs in its file ALSO fails, so the
list only ever shrinks as hardcodes get removed.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What we scan: code + config, not prose docs, static marketing pages, lockfiles, tests.
SCAN_DIRS = ("backend/", "scripts/", "tools/", "config/", "docker/", "companion-module-mxl-switcher/main.js")
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".mp4", ".md", ".lock", ".companionconfig")
SKIP_NAMES = {"package-lock.json"}

IPV4 = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
HOSTS = re.compile(r"(?:prodbots\.com|mxlswitcher\.com|cochran\.cloud)")
# never a "site" address: loopback / wildcard / the standard docker bridge gateway
UNIVERSAL_OK = {"127.0.0.1", "0.0.0.0", "172.17.0.1"}

# file -> literals permitted there (default values behind env vars, docs-in-comments, ...)
ALLOW = {
    "config/facility.json": {"10.0.0.5"},  # manifest defaults; MXL_VM_IP etc. override
    # Core-defaults neutralized Oct 9: the runnable Core no longer defaults to OUR hosts
    # (prodbots.com / *.cochran.cloud) — defaults are now neutral (localhost / YOUR-*-HOST),
    # overridable via MXL_* env vars. Only private-range IP defaults remain allowlisted.
    "scripts/bring-up-mxl.sh": {"203.0.113.50", "192.168.8.177"},  # env defaults; site IP is an RFC 5737 placeholder
    "scripts/mxl-doctor": {"10.0.0.4"},                           # comment only
    "tools/adapters.py": {"10.0.0.5"},                            # default rtsp_host arg (pinned by launch-parity test)
    "tools/mv_encode.py": {"10.0.0.4"},                           # MXL_VM1_IP default
    "tools/guest-leg-doctor.sh": {"10.0.0.4", "10.0.0.5"},        # MXL_VM1_IP/VM2_IP defaults
    "scripts/quickstart.sh": {"mxlswitcher.com"},                 # comment only
    "tools/start-jonas-leg.sh": {"10.0.0.4"},                     # MXL_VM1_IP default
    "tools/tams_shipper.py": {"203.0.113.140"},                   # TAMS_HOST default (RFC 5737 placeholder)
    "tools/backfill-mini.py": {"203.0.113.140"},                  # TAMS_HOST default (RFC 5737 placeholder)
    "companion-module-mxl-switcher/main.js": {"prodbots.com"},    # user-editable config field default
}


def _tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        if rel in SKIP_NAMES or rel.endswith(SKIP_SUFFIXES):
            continue
        if not rel.startswith(SCAN_DIRS):
            continue
        yield rel


def _found():
    found = {}
    for rel in _tracked():
        try:
            text = (ROOT / rel).read_text(errors="ignore")
        except OSError:
            continue
        lits = {m.group(0) for m in IPV4.finditer(text)} - UNIVERSAL_OK
        lits |= {m.group(0) for m in HOSTS.finditer(text)}
        if lits:
            found[rel] = lits
    return found


def test_no_new_hardcoded_hosts():
    bad = {rel: sorted(lits - ALLOW.get(rel, set())) for rel, lits in _found().items()
           if lits - ALLOW.get(rel, set())}
    assert not bad, (
        "New hardcoded IP/host literal(s) — move to an env var with the old value as default "
        "(docs/CONFIG.md), or allowlist with a reason in tests/test_no_hardcoded_hosts.py:\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in sorted(bad.items())))


def test_allowlist_has_no_stale_entries():
    found = _found()
    stale = {rel: sorted(allowed - found.get(rel, set())) for rel, allowed in ALLOW.items()
             if allowed - found.get(rel, set())}
    assert not stale, "allowlist entries no longer present (remove them — it's a ratchet):\n  " + \
        "\n  ".join(f"{k}: {v}" for k, v in sorted(stale.items()))


def test_scanner_catches_a_new_literal(tmp_path):
    """Self-test so the tripwire can't silently go blind."""
    assert IPV4.search("host = '8.8.8.8'")
    assert IPV4.search("http://203.0.113.140:9000")
    assert not IPV4.search("version 1.5.15")
    assert not IPV4.search("v1.2.3.4.5")
    assert HOSTS.search("https://prodbots.com/api")
