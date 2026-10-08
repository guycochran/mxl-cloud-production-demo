# SPDX-License-Identifier: Apache-2.0
"""scripts/check-dco.sh: passes signed-off commits, fails missing/mismatched sign-offs,
ignores merge commits. Runs against a throwaway git repo (no network)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-dco.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git + bash")


def _git(repo, *args, email="dev@example.com", name="Dev"):
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
           "HOME": str(repo), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=env, check=True).stdout.strip()


def _commit(repo, fname, msg, **kw):
    (repo / fname).write_text(fname)
    _git(repo, "add", fname, **kw)
    _git(repo, "commit", "-q", "-m", msg, **kw)


def _check(repo, base, head="HEAD"):
    return subprocess.run(["bash", str(SCRIPT), base, head], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "base.txt", "base")
    _git(tmp_path, "branch", "base")
    return tmp_path


def test_signed_off_commits_pass(repo):
    _commit(repo, "a.txt", "feat: a\n\nSigned-off-by: Dev <dev@example.com>")
    _commit(repo, "b.txt", "fix: b\n\nSigned-off-by: Dev <DEV@example.com>")  # case-insensitive
    r = _check(repo, "base")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 commit(s) checked" in r.stdout


def test_missing_signoff_fails(repo):
    _commit(repo, "a.txt", "feat: a\n\nSigned-off-by: Dev <dev@example.com>")
    _commit(repo, "b.txt", "fix: no trailer")
    r = _check(repo, "base")
    assert r.returncode == 1
    assert "missing Signed-off-by" in r.stdout and "fix: no trailer" in r.stdout


def test_mismatched_signoff_fails(repo):
    _commit(repo, "a.txt", "feat: a\n\nSigned-off-by: Someone Else <other@example.com>")
    r = _check(repo, "base")
    assert r.returncode == 1
    assert "does not match author" in r.stdout


def test_merge_commits_are_ignored(repo):
    _git(repo, "checkout", "-q", "-b", "side", "base")
    _commit(repo, "s.txt", "side\n\nSigned-off-by: Dev <dev@example.com>")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge side (no sign-off on the merge itself)", "side")
    r = _check(repo, "base")
    assert r.returncode == 0, r.stdout


def test_usage_error_without_base(repo):
    r = subprocess.run(["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 2
