"""Runs the dependency-free Node tests under tests/js/ (backend auth + rate limit).

Skipped automatically when `node` isn't installed, so the Python suite still runs
anywhere. CI also runs `node --test` directly (see .github/workflows/ci.yml).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_TESTS = sorted((ROOT / "tests" / "js").glob("*.test.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_backend_tests_pass():
    assert JS_TESTS, "no tests/js/*.test.js found"
    r = subprocess.run(["node", "--test", *map(str, JS_TESTS)],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
