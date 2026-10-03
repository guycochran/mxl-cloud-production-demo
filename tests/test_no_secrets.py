"""Guard against committing secrets to this public repo.

Not a replacement for a real scanner (CI also runs one), but a fast, dependency-free
tripwire for the specific mistakes this repo has been close to: a credential baked into
a URL (the cam ingests once had `rtsp://admin:Password@…`), or a private key / token
literal. Runs in the normal pytest suite so a bad commit fails before it lands.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_NAMES = {"test_no_secrets.py"}  # this file names the patterns on purpose

# credential embedded in a URL:  scheme://user:pass@host  (ignore obvious placeholders)
CRED_IN_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:([^/\s:@]+)@")
PLACEHOLDER = re.compile(r"(user|pass|example|placeholder|your|xxx|\$\{|<)", re.I)

KEY_MARKERS = (
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
)

# a secret ASSIGNED to a string literal — e.g.  aws_secret_access_key = "AKIA…".
# (Passing a variable, `aws_secret_access_key=_pw`, is correct and must NOT trip.)
HARDCODED_SECRET = re.compile(
    r"(aws_secret_access_key|secret_key|api_key|password|passwd|token)\s*[:=]\s*"
    r"""['"][^'"\s${<]{6,}['"]""",
    re.I,
)


def _tracked_files():
    """Only files git actually tracks — gitignored working copies (e.g. the local
    backend/server-enhanced.js with live tokens) are never committed, so scanning them
    would be a false positive about the PUBLIC repo."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


def _scan_files():
    for p in _tracked_files():
        if not p.is_file() or p.name in SKIP_NAMES:
            continue
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".wasm",
                                ".woff", ".woff2", ".mp4", ".pdf"}:
            continue
        try:
            yield p, p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue


def test_no_credentials_in_urls():
    hits = []
    for path, text in _scan_files():
        for m in CRED_IN_URL.finditer(text):
            secret = m.group(1)
            if PLACEHOLDER.search(secret):
                continue
            hits.append(f"{path.relative_to(ROOT)}: {m.group(0)}")
    assert not hits, "credential-in-URL found:\n  " + "\n  ".join(hits)


def test_no_private_keys_or_token_markers():
    hits = []
    for path, text in _scan_files():
        for marker in KEY_MARKERS:
            if marker in text:
                hits.append(f"{path.relative_to(ROOT)}: {marker}")
    assert not hits, "secret marker found:\n  " + "\n  ".join(hits)


def test_no_hardcoded_secret_assignments():
    """A secret assigned to a string literal. Variable passing (reading from env or a
    file outside the repo) is correct and must not trip this."""
    hits = []
    for path, text in _scan_files():
        for m in HARDCODED_SECRET.finditer(text):
            hits.append(f"{path.relative_to(ROOT)}: {m.group(0)[:60]}")
    assert not hits, "hardcoded secret assignment found:\n  " + "\n  ".join(hits)
