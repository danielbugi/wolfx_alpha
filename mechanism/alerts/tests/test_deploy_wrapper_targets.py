# mechanism/alerts/tests/test_deploy_wrapper_targets.py
"""Regression coverage for the Phase 3 finding that prompted P1 item 3: donchian-docker-firewall.service
was committed while pointing at a script that existed only on the live VPS, not in git -- so a fresh
checkout could never reproduce what was actually running. deploy/vps/README.md documents every
tracked script's installed path; this test enforces the invariant going forward for the
/opt/donchian/scripts/ convention every wrapper script here follows (run_pipeline.sh,
run_postmarket_retry.sh, run_channel_sender.sh, firstlight1_updateonly.sh, and the proposed
run_bot_service.sh -- P1 item 6): every unit file's ExecStart=/ExecStop= target under
/opt/donchian/scripts/<name> must resolve to a tracked, executable file named <name> in
deploy/vps/ or deploy/db/, and any bash script referenced this way must be syntactically valid.
No live VPS or Postgres needed -- pure filesystem/subprocess checks against the repo itself.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIRS = [REPO_ROOT / "deploy" / "vps", REPO_ROOT / "deploy" / "db"]
SCRIPTS_PREFIX = "/opt/donchian/scripts/"

EXEC_LINE = re.compile(r"^Exec(?:Start|Stop)=(.*)$", re.MULTILINE)


def _unit_files():
    files = []
    for d in DEPLOY_DIRS:
        files += sorted(d.glob("*.service"))
        files += sorted(d.glob("*.service.proposed"))
    return files


def _referenced_script_names(unit_text: str):
    names = []
    for line in EXEC_LINE.findall(unit_text):
        line = line.strip()
        for token in line.split():
            if token.startswith(SCRIPTS_PREFIX):
                names.append(token[len(SCRIPTS_PREFIX):])
    return names


def _git_tracked_mode(path: Path) -> str:
    """The exec bit as git actually stores it in the index/blob, not the local filesystem's stat()
    -- NTFS on a Windows dev machine doesn't preserve POSIX exec bits at all, so stat() would report
    every file as non-executable there even though git (and the Linux CI runner / VPS that actually
    checks it out) sees 100755. '100755' or '100644'."""
    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", str(path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if not result.stdout.strip():
        return ""
    return result.stdout.split()[0]  # e.g. "100755"


@pytest.mark.parametrize("unit_file", _unit_files(), ids=lambda p: p.name)
def test_scripts_path_targets_exist_and_are_executable(unit_file: Path):
    text = unit_file.read_text(encoding="utf-8")
    names = _referenced_script_names(text)
    if not names:
        pytest.skip(f"{unit_file.name} has no /opt/donchian/scripts/ ExecStart/ExecStop target")
    for name in names:
        candidates = [d / name for d in DEPLOY_DIRS]
        found = [c for c in candidates if c.is_file()]
        assert found, (
            f"{unit_file.name} references {SCRIPTS_PREFIX}{name}, "
            f"but no tracked file named '{name}' exists in {[str(d) for d in DEPLOY_DIRS]}"
        )
        script = found[0]
        mode = _git_tracked_mode(script)
        assert mode == "100755", (
            f"{script} is referenced as an ExecStart/ExecStop target but git tracks it as mode "
            f"{mode or '<untracked>'}, not 100755 -- run 'git update-index --chmod=+x {script}'"
        )


def _bash_actually_works() -> bool:
    if shutil.which("bash") is None:
        return False
    try:
        result = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, text=True, timeout=10)
        return result.returncode == 0 and result.stdout.strip() == "ok"
    except Exception:
        return False


@pytest.mark.skipif(not _bash_actually_works(), reason="no working bash on this runner (e.g. a broken WSL relay on Windows dev machines)")
@pytest.mark.parametrize("script", sorted(
    p for d in DEPLOY_DIRS for p in d.glob("*.sh")
), ids=lambda p: p.name)
def test_wrapper_scripts_have_valid_bash_syntax(script: Path):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"{script}: {result.stderr}"
