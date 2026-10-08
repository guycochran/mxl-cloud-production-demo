"""srt-listen SRT passphrase: the public guest listener can require encryption.

Covers the three places the passphrase flows through in srt-listen mode:
  * SrtListenerGuestAdapter / guest_ingest.py  (single-port listener)
  * tools/guest_av_listen.sh                   (the A/V fan-out that owns the public port)
  * scripts/quickstart.sh                      (validation, warnings, hand-off to the fan-out)
Unset = previous behaviour (open listener, byte-identical launch string) + a loud warning.
Hardware-free: gi is stubbed (conftest.py) and gst-launch-1.0 is a PATH stub.
"""
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

import contribution_core as cc
from adapters import SrtListenerGuestAdapter, srt_listen_passphrase

ROOT = Path(__file__).resolve().parent.parent
QS = (ROOT / "scripts" / "quickstart.sh").read_text()
FANOUT = ROOT / "tools" / "guest_av_listen.sh"
BASH = shutil.which("bash")
SH = shutil.which("dash") or shutil.which("sh")

GOOD = "Ab-c.d_e~f-0123456789"  # every allowed punctuation char
BAD = ["short", "x" * 80, "has space-123456", "has'quote-123456", 'has"quote-12345',
       "back\\slash-12345", "bang!bang-123456", "semi;colon-123456", "dollar$sign-12345",
       "plus+sign-123456", "pct%41-123456789", "amp&mode=caller-1", "slash/colon:at@-1"]


def _fid(n=1):
    return f"9e111e00-aaaa-4bbb-8ccc-00000000000{n}"


# ── adapter ──────────────────────────────────────────────────────────────────
def test_adapter_default_has_no_passphrase():
    a = SrtListenerGuestAdapter(path="guest1", flow_id=_fid(), label="Guest 1")
    assert "passphrase" not in a.source_fragment()


def test_adapter_emits_passphrase_on_the_srtsrc_only():
    a = SrtListenerGuestAdapter(path="guest1", flow_id=_fid(), label="Guest 1",
                                listen_port=8890, passphrase=GOOD)
    frag = a.source_fragment()
    assert frag.startswith(
        f'srtsrc uri="srt://0.0.0.0:8890?mode=listener&latency=300" passphrase="{GOOD}" ! tsdemux')
    assert frag.count("passphrase") == 1
    assert GOOD not in a.description  # secret never lands in flow metadata


def test_core_launch_with_passphrase_keeps_back_half_identical():
    plain = cc.ContributionCore(SrtListenerGuestAdapter(
        path="guest1", flow_id=_fid(), label="Guest 1", listen_port=8990), repair_url="")
    locked = cc.ContributionCore(SrtListenerGuestAdapter(
        path="guest1", flow_id=_fid(), label="Guest 1", listen_port=8990, passphrase=GOOD),
        repair_url="")
    assert locked.pipe._launch == plain.pipe._launch.replace(
        'latency=300" ', f'latency=300" passphrase="{GOOD}" ', 1)
    assert "mxlsink" in locked.pipe._launch and GOOD not in locked.pipe._launch.split("mxlsink", 1)[1]


@pytest.mark.parametrize("bad", BAD)
def test_adapter_rejects_invalid_passphrase(bad):
    with pytest.raises(ValueError):
        srt_listen_passphrase(bad)
    with pytest.raises(ValueError):
        SrtListenerGuestAdapter(path="guest1", flow_id=_fid(), label="Guest 1", passphrase=bad)


def test_empty_passphrase_means_open():
    assert srt_listen_passphrase("") == "" and srt_listen_passphrase(None) == ""


# ── guest_ingest.py (srt-listen entrypoint) ──────────────────────────────────
class _FakeCore:
    last = None

    def __init__(self, adapter, **_kw):
        _FakeCore.last = adapter

    def run(self):
        pass


def _run_ingest(monkeypatch, passphrase):
    monkeypatch.setattr(cc, "ContributionCore", _FakeCore)
    monkeypatch.setenv("MXL_GUEST_TRANSPORT", "srt-listen")
    monkeypatch.setenv("MXL_GUEST_LISTEN_PORT", "8890")
    monkeypatch.setenv("MXL_GUEST_LISTEN_HOST", "0.0.0.0")
    if passphrase is None:
        monkeypatch.delenv("MXL_GUEST_SRT_PASSPHRASE", raising=False)
    else:
        monkeypatch.setenv("MXL_GUEST_SRT_PASSPHRASE", passphrase)
    monkeypatch.setattr("sys.argv", ["guest_ingest.py", "guest1", _fid(), "Guest 1", "1000"])
    _FakeCore.last = None
    runpy.run_path(str(ROOT / "tools" / "guest_ingest.py"), run_name="__main__")
    return _FakeCore.last


def test_guest_ingest_applies_passphrase(monkeypatch, capsys):
    a = _run_ingest(monkeypatch, GOOD)
    assert f'passphrase="{GOOD}"' in a.source_fragment()
    err = capsys.readouterr().err
    assert "REQUIRED" in err and GOOD not in err


def test_guest_ingest_unset_is_open_and_warns(monkeypatch, capsys):
    a = _run_ingest(monkeypatch, None)
    assert "passphrase" not in a.source_fragment()
    assert "WARNING" in capsys.readouterr().err


def test_guest_ingest_invalid_passphrase_fails_closed(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run_ingest(monkeypatch, "has space-123456")
    assert "MXL_GUEST_SRT_PASSPHRASE invalid" in str(e.value)
    assert _FakeCore.last is None  # never started an open listener


# ── tools/guest_av_listen.sh (A/V fan-out, owns the public port) ─────────────
def _run_fanout(tmp_path, passphrase):
    """Run the fan-out with a stub gst-launch-1.0 that records its argv (one word per
    line) and then kills the supervising loop."""
    bindir = tmp_path / "bin"; bindir.mkdir()
    argv_log = tmp_path / "argv"
    stub = bindir / "gst-launch-1.0"
    stub.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$ARGV_LOG"\n'
                    'kill -TERM "$PPID"\n')
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "ARGV_LOG": str(argv_log)}
    if passphrase is not None:
        env["MXL_GUEST_SRT_PASSPHRASE"] = passphrase
    r = subprocess.run([SH, str(FANOUT), "8890", "8990", "9090", "300"], env=env,
                       capture_output=True, text=True, timeout=20)
    words = argv_log.read_text().splitlines() if argv_log.exists() else None
    return r, words


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_fanout_passes_passphrase_as_its_own_argv_word(tmp_path):
    r, words = _run_fanout(tmp_path, GOOD)
    assert words is not None, r.stderr
    i = words.index("srtsrc")
    assert words[i + 1] == "uri=srt://0.0.0.0:8890?mode=listener&latency=300"
    assert words[i + 2] == f"passphrase={GOOD}"
    assert sum(w.startswith("passphrase=") for w in words) == 1  # loopback legs stay plain
    assert "REQUIRED" in r.stderr and GOOD not in r.stderr


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_fanout_unset_is_unchanged_and_warns(tmp_path):
    r, words = _run_fanout(tmp_path, None)
    assert words is not None, r.stderr
    assert not any(w.startswith("passphrase") for w in words)
    assert "WARNING" in r.stderr and "OPEN" in r.stderr


@pytest.mark.skipif(SH is None, reason="sh not available")
@pytest.mark.parametrize("bad", BAD)
def test_fanout_invalid_passphrase_exits_before_listening(tmp_path, bad):
    r, words = _run_fanout(tmp_path, bad)
    assert r.returncode == 2 and words is None  # gst-launch never ran
    assert "MXL_GUEST_SRT_PASSPHRASE" in r.stderr


# ── scripts/quickstart.sh ────────────────────────────────────────────────────
def _block():
    m = re.search(r"# --- BEGIN guest-srt-conf.*?\n(.*?)\n# --- END guest-srt-conf ---", QS, re.S)
    assert m
    return m.group(1)


def _listen_check(env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("MXL_")}
    e.update(env)
    script = _block() + '\nsrt_listen_pass_check; rc=$?\necho "REQ=[$SRT_LISTEN_REQUIRED] OPEN=[$SRT_LISTEN_OPEN]"\nexit $rc'
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=e)


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_quickstart_listen_unset_warns_loudly():
    r = _listen_check({})
    assert r.returncode == 0
    assert "WARNING" in r.stdout and "OPEN" in r.stdout
    assert "REQ=[] OPEN=[guest1 guest2 ]" in r.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_quickstart_listen_shared_and_per_guest():
    r = _listen_check({"MXL_GUEST_SRT_PASSPHRASE": GOOD})
    assert r.returncode == 0 and "REQ=[guest1 guest2 ] OPEN=[]" in r.stdout
    assert "WARNING" not in r.stdout and GOOD not in r.stdout
    r = _listen_check({"MXL_GUEST1_SRT_PASSPHRASE": GOOD})
    assert r.returncode == 0 and "REQ=[guest1 ] OPEN=[guest2 ]" in r.stdout
    assert "WARNING" in r.stdout  # a partially-open setup still warns


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("bad", BAD)
def test_quickstart_listen_rejects_invalid(bad):
    r = _listen_check({"MXL_GUEST2_SRT_PASSPHRASE": bad})
    assert r.returncode == 1 and "srt-listen" in r.stderr


def test_quickstart_listen_branch_enforces_check():
    assert "srt_listen_pass_check || exit 1" in QS
    assert "is ignored in srt-listen mode" not in QS  # the old "we drop it" message is gone


def test_quickstart_fanout_gets_passphrase_via_env_inheritance_only():
    m = re.search(r"run_guest_fanout\(\)\{.*?\n  \}\n", QS, re.S)
    assert m, "run_guest_fanout not found"
    body = m.group(0)
    assert 'pass=$(_srt_pass_for "$5")' in body
    assert 'MXL_GUEST_SRT_PASSPHRASE="$pass" docker run' in body
    assert "-e MXL_GUEST_SRT_PASSPHRASE " in body          # name only: value not in argv
    assert '-e "MXL_GUEST_SRT_PASSPHRASE=' not in body
    assert 'run_guest_fanout guest1 "$_g1pub" "$_g1v" "$_g1a" 1' in QS
    assert 'run_guest_fanout guest2 "$_g2pub" "$_g2v" "$_g2a" 2' in QS


def test_quickstart_legs_never_get_the_passphrase():
    for fn in ("run_guest", "run_guest_audio"):
        m = re.search(fn + r"\(\)\{.*?\n  \}\n", QS, re.S)
        assert m and "SRT_PASSPHRASE" not in m.group(0)
