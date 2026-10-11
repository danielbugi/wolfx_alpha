"""The unattended checkpoint runners (ops/checkpoint/checkpoint.sh and r13_confirm.sh) are EXECUTED here under stubs, not just parsed.

Why: `bash -n` accepted a script whose production run aborted at once. An apostrophe inside a double-quoted `${VAR:?word}` is read as the start of a quote by some bash
builds, so the assignment swallowed the next lines ("PIPELINE_START='...': command not found", "DIAG: unbound variable"). Nothing executed the file before it ran on the VPS.
These tests (a) forbid that construct and (b) run both scripts end to end in self-test mode with stub `docker`/`systemctl`, asserting that they start, evaluate, print BOTH
verdicts, produce no shell error, and never print two contradictory lines for the same evidence check (the `cmd && ev PASS || ev FAIL` trap).
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
OPS = REPO / "ops" / "checkpoint"
BUNDLE = ("checkpoint.sh", "assert_forward_research.sql", "assert_collector_readiness.sql", "r13_confirm.sh")
BASH = shutil.which("bash")
PY3 = shutil.which("python3")

needs_linux = pytest.mark.skipif(BASH is None or PY3 is None or sys.platform == "win32",
                                 reason="executes the scripts with stub binaries on a POSIX PATH: Linux only (CI runner, VPS); the static check below runs everywhere")

STUB_DOCKER = r'''#!/bin/sh
# docker stub: nothing is real. Drains stdin, prints nothing except the image names the wrapper compares.
case "$1" in
  inspect) for a; do last=$a; done
           case "$last" in donchian-screener-bot-1) echo ghcr.io/danielbugi/wolfx_alpha-mechanism:e3feb64d3e86 ;; donchian-screener-backend-1) echo ghcr.io/danielbugi/wolfx_alpha-backend:963ab19318b7 ;; *) echo healthy ;; esac ;;
  *) cat >/dev/null 2>&1 ;;
esac
exit 0
'''
STUB_SYSTEMCTL = r'''#!/bin/sh
case "$1" in
  show) for a; do last=$a; done; case "$*" in *Result*) echo success ;; *ExecMainStatus*) echo 0 ;; *) echo "Sun 2026-10-11 03:20:28 UTC" ;; esac ;;
  is-enabled) echo disabled ;;
  is-active) echo inactive ;;
esac
exit 0
'''


def make_bundle(tmp_path):
    d = tmp_path / "bundle"
    d.mkdir()
    sums = []
    for name in BUNDLE:
        data = (OPS / name).read_bytes().replace(b"\r\n", b"\n")
        (d / name).write_bytes(data)
        (d / name).chmod(0o755)
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (d / "SHA256SUMS").write_bytes(("\n".join(sums) + "\n").encode("utf-8"))          # LF only: sha256sum -c rejects CRLF-terminated lines
    return d


def make_stubs(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("systemctl", STUB_SYSTEMCTL)):
        (b / name).write_text(body, encoding="utf-8", newline="\n")
        (b / name).chmod(0o755)
    base = tmp_path / "base"
    (base / "logs").mkdir(parents=True)
    (base / "CURRENT_MECHANISM_SHA").write_text("5c511899505a\n", encoding="utf-8")
    return b, base


def base_env(stubs, base):
    return dict(os.environ, PATH=stubs.as_posix() + os.pathsep + os.environ.get("PATH", ""), DONCHIAN_BASE=base.as_posix(), CHECKPOINT_SELFTEST="1",
                CHECKPOINT_SESSION="2026-10-09", CHECKPOINT_PIN="5c511899505a", CHECKPOINT_PIPELINE_START="2026-10-09T20:00:00Z")


ERRORS = ("unbound variable", "command not found", "syntax error", "unexpected EOF", "No such file or directory")


def test_no_apostrophe_sits_inside_a_double_quoted_parameter_expansion_word():
    offenders = []
    for p in sorted(OPS.glob("*.sh")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r'\$\{[A-Za-z_][A-Za-z_0-9]*:[-=?+]([^}]*)\}', line):
                if "'" in m.group(1):
                    offenders.append(f"{p.name}:{n}: {m.group(0)[:80]}")
    assert not offenders, offenders


@needs_linux
def test_the_checkpoint_runner_executes_in_self_test_mode_and_prints_both_verdicts(tmp_path):
    bundle = make_bundle(tmp_path)
    stubs, base = make_stubs(tmp_path)
    env = dict(base_env(stubs, base), CHECKPOINT_DIR=bundle.as_posix(), CHECKPOINT_OUT=(tmp_path / "out").as_posix())
    r = subprocess.run([BASH, (bundle / "checkpoint.sh").as_posix()], capture_output=True, text=True, env=env, timeout=120)
    out = r.stdout + r.stderr
    for bad in ERRORS:
        assert bad not in out, (bad, out[-1500:])
    assert r.returncode in (1, 3, 4), (r.returncode, out[-800:])                 # stubs give no database rows, so the verdicts are not clean, but the runner RAN
    assert "CHECKPOINT|start_utc=" in out and "INTEGRITY|bundle verified" in out and "READONLY-PROOF|" in out
    assert re.search(r"^VERDICT_A\|", out, re.M) and re.search(r"^VERDICT_B\|", out, re.M)
    assert (tmp_path / "out" / "latest_selftest.status").exists()


@needs_linux
def test_the_checkpoint_runner_refuses_a_tampered_bundle(tmp_path):
    bundle = make_bundle(tmp_path)
    stubs, base = make_stubs(tmp_path)
    (bundle / "assert_forward_research.sql").write_text("select 1;\n", encoding="utf-8")
    env = dict(base_env(stubs, base), CHECKPOINT_DIR=bundle.as_posix(), CHECKPOINT_OUT=(tmp_path / "out").as_posix())
    r = subprocess.run([BASH, (bundle / "checkpoint.sh").as_posix()], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 5 and "bundle hash mismatch" in r.stdout + r.stderr


@needs_linux
def test_the_confirmation_wrapper_executes_and_never_prints_contradictory_evidence_lines(tmp_path):
    bundle = make_bundle(tmp_path)
    stubs, base = make_stubs(tmp_path)
    env = dict(base_env(stubs, base), R13_DIR=bundle.as_posix(), R13_BASE=base.as_posix(), R13_OUT=(tmp_path / "r13").as_posix())
    r = subprocess.run([BASH, (bundle / "r13_confirm.sh").as_posix()], capture_output=True, text=True, env=env, timeout=180)
    out = r.stdout + r.stderr
    for bad in ERRORS:
        assert bad not in out, (bad, out[-1500:])
    assert "R13|bundle verified" in out and re.search(r"^EVID_VERDICT\|", out, re.M) and re.search(r"^VERDICT_A\|", out, re.M) and re.search(r"^VERDICT_B\|", out, re.M)
    names = [m.group(1) for m in re.finditer(r"^EVID\|([^|]+)\|(?:PASS|FAIL|REVIEW)", out, re.M)]
    assert names and len(names) == len(set(names)), [n for n in names if names.count(n) > 1]      # each check reports exactly once
    assert re.search(r"^EVID\|collector_still_disabled_and_unarmed\|PASS", out, re.M)
    assert re.search(r"^EVID\|bot_and_backend_images_unchanged\|PASS", out, re.M)
