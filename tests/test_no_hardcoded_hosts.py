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
import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What we scan for site hostnames + IP literals: code, config, and the served web pages.
# Not prose docs, static marketing pages, lockfiles, tests, or vendored third-party JS.
# (Prose docs and the web pages are ALSO covered by the public-IPv4 sweep further down.)
SCAN_DIRS = ("backend/", "scripts/", "tools/", "config/", "docker/", "web/",
             "companion-module-mxl-switcher/main.js")
SKIP_PREFIXES = ("web/vendor/",)
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".mp4", ".md", ".lock", ".companionconfig",
                 ".woff", ".woff2", ".ico", ".svg")
SKIP_NAMES = {"package-lock.json"}

IPV4 = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
HOSTS = re.compile(r"(?:prodbots\.com|mxlswitcher\.com|cochran\.cloud)")
# never a "site" address: loopback / wildcard / the standard docker bridge gateway
UNIVERSAL_OK = {"127.0.0.1", "0.0.0.0", "172.17.0.1"}

# file -> literals permitted there (default values behind env vars, docs-in-comments, ...)
ALLOW = {
    "config/facility.json": {"10.0.0.5"},  # manifest defaults; MXL_VM_IP etc. override
    "scripts/bring-up-mxl.sh": {"203.0.113.50", "192.168.8.177"},  # env defaults (MXL_*); site IP is an RFC 5737 placeholder
    "scripts/mxl-doctor": {"10.0.0.4", "prodbots.com"},          # comment only
    "tools/adapters.py": {"10.0.0.5"},                            # default rtsp_host arg (pinned by launch-parity test)
    "tools/nmos_node.py": {"prodbots.com"},                       # --facility default (+ usage); follow-up once the IS-05 work lands
    "tools/contribution_core.py": {"prodbots.com"},               # comment example for MXL_REPAIR_URL
    "tools/mv_encode.py": {"10.0.0.4"},                           # MXL_VM1_IP default
    "tools/guest-leg-doctor.sh": {"10.0.0.4", "10.0.0.5"},        # MXL_VM1_IP/VM2_IP defaults
    "scripts/quickstart.sh": {"mxlswitcher.com"},                 # comment only
    "tools/start-jonas-leg.sh": {"10.0.0.4"},                     # MXL_VM1_IP default
    "tools/tams_shipper.py": {"203.0.113.140"},                   # TAMS_HOST default (RFC 5737 placeholder)
    "tools/backfill-mini.py": {"203.0.113.140"},                  # TAMS_HOST default (RFC 5737 placeholder)
    "web/mxl.html": {"203.0.113.32"},                             # guest SRT host shown to visitors (RFC 5737 placeholder)
    "web/lower-third.html": {"prodbots.com"},                     # keyer graphic polls the backend cross-origin; follow-up
    "web/mxl-archive.html": {"mxlswitcher.com"},                  # links to the public project/docs site
    "web/decks/deck-live.html": {"mxlswitcher.com"},              # links to the public project/docs site
    "web/decks/deck-recap.html": {"mxlswitcher.com"},             # links to the public project/docs site
}


def _tracked():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        if rel in SKIP_NAMES or rel.endswith(SKIP_SUFFIXES) or rel.startswith(SKIP_PREFIXES):
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


# ── Public-IPv4 sweep: EVERY tracked text file (docs, README, web pages, code) ─────────
# The per-file allowlist above guards code/config. This sweep is broader and simpler: no
# internet-routable IPv4 literal may appear anywhere in the tracked tree. Allowed ranges:
# private (RFC 1918), loopback, link-local, unspecified, shared CGNAT, and the RFC 5737
# documentation ranges used as placeholders (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).
# tests/ is excluded (it uses fake public client IPs on purpose) and so is vendored JS.
SAFE_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",       # RFC 1918 private
    "127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/8",          # loopback, link-local, unspecified
    "100.64.0.0/10",                                       # shared address space (CGNAT)
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",   # RFC 5737 documentation placeholders
    "255.255.255.255/32",
)]
# Public literals that are genuinely meant to be here (well-known public resolvers etc.).
# Keep this empty unless there is a real reason; facility addresses never belong here.
PUBLIC_IPV4_ALLOW = set()
SWEEP_SKIP_PREFIXES = ("tests/", "web/vendor/", ".git/")
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm", ".ico", ".woff", ".woff2",
                   ".ttf", ".otf", ".pdf", ".zip", ".gz")


def _is_safe_ipv4(lit):
    ip = ipaddress.ip_address(lit)
    return lit in PUBLIC_IPV4_ALLOW or any(ip in n for n in SAFE_NETS)


def _public_ipv4_hits():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout
    hits = {}
    for rel in out.splitlines():
        if rel.startswith(SWEEP_SKIP_PREFIXES) or rel.lower().endswith(BINARY_SUFFIXES):
            continue
        try:
            text = (ROOT / rel).read_text(errors="ignore")
        except OSError:
            continue
        bad = {m.group(0) for m in IPV4.finditer(text) if not _is_safe_ipv4(m.group(0))}
        if bad:
            hits[rel] = sorted(bad)
    return hits


def test_no_public_ipv4_anywhere_in_tracked_text():
    hits = _public_ipv4_hits()
    assert not hits, (
        "Internet-routable IPv4 literal(s) in tracked files — replace with an RFC 5737 placeholder "
        "(e.g. 203.0.113.10) or an env var:\n  " + "\n  ".join(f"{k}: {v}" for k, v in sorted(hits.items())))


def test_ipv4_sweep_self_test():
    """The sweep must flag a routable address and pass the placeholder/private ranges."""
    assert not _is_safe_ipv4("8.8.4.4")
    assert not _is_safe_ipv4("20.1.2.3")
    for ok in ("203.0.113.10", "198.51.100.7", "192.0.2.1", "10.0.0.4", "172.17.0.1",
               "192.168.8.177", "127.0.0.1", "0.0.0.0", "169.254.1.1"):
        assert _is_safe_ipv4(ok), ok


# ── No static QR codes in the served web tree ───────────────────────────────────────────
# A QR image encodes its address in pixels, so the text scans above can't see inside it.
# Static Larix QR PNGs once baked a real facility address into web/. The served pages now
# fetch a per-box QR from GET /api/mxl/ingest/qr/guestN.png at runtime, so no QR image
# should ever be committed under web/ again.
def test_no_static_qr_images_in_web():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "web/"], capture_output=True, text=True, check=True).stdout
    qr = [rel for rel in out.splitlines()
          if rel.lower().endswith(BINARY_SUFFIXES + (".svg",)) and re.search(r"(^|[/_.-])qr", rel.lower())]
    assert not qr, ("Static QR image(s) committed under web/ — serve them from "
                    "/api/mxl/ingest/qr/<slot>.png instead: " + ", ".join(qr))
