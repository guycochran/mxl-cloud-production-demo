# SPDX-License-Identifier: Apache-2.0
"""Docs-link sanity: every relative Markdown link to a repo file must resolve.

An adoptable reference implementation's docs are a promise; a 404 relative link is a
broken promise the first newcomer hits. This walks the Markdown files and asserts that
every link pointing at a repo-relative path (not http(s):, not #anchor, not mailto:)
actually exists on disk. External URLs are NOT fetched here (no network in CI); a
separate optional job can link-check those.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _markdown_files():
    skip = {".git", "node_modules", "__pycache__"}
    for p in ROOT.rglob("*.md"):
        if not any(part in skip for part in p.parts):
            yield p


def test_relative_markdown_links_resolve():
    broken = []
    for md in _markdown_files():
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in MD_LINK.finditer(text):
            target = m.group(1).strip()
            # strip optional title: [x](path "title")
            target = target.split(" ", 1)[0]
            if (not target
                    or target.startswith(("http://", "https://", "#", "mailto:", "tel:",
                                          "data:", "//"))):
                continue
            # drop any #anchor / ?query on a local path
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)
