"""The dormant paper-tracker wrapper (deploy/vps/run_paper_tracker.sh) and its proposed unit/timer: every refusal path happens BEFORE any container is started, the job has its own pin and arming file and
never touches the collector's, and what it runs is exactly the runner's `--next` with the pinned image. Behaviour tests run the real script against a stub `docker` (Linux only)."""
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
VPS = REPO / "deploy" / "vps"
WRAPPER = VPS / "run_paper_tracker.sh"
PIN = "a" * 12
linux_only = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None or shutil.which("flock") is None, reason="needs POSIX bash + flock (CI / VPS)")


def text(p):
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def world(tmp_path):
    base = tmp_path / "donchian"
    (base / "compose").mkdir(parents=True)
    (base / "env").mkdir()
    (base / "env" / ".env").write_text("X=1\n")
    for f in ("docker-compose.yml", "docker-compose.prod.yml"):
        (base / "compose" / f).write_text("services: {}\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "docker.log"
    exit_file = tmp_path / "docker_exit"
    exit_file.write_text("0")
    d = stub / "docker"
    d.write_text(f"""#!/usr/bin/env bash
echo "ARGS: $*" >> "{log}"
echo "ENV: IMAGE_TAG=${{IMAGE_TAG-<unset>}}" >> "{log}"
exit "$(cat {exit_file})"
""")
    d.chmod(d.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", DONCHIAN_BASE=str(base))
    return {"base": base, "env": env, "log": log, "exit": exit_file}


def run(world, mode=None):
    cmd = ["bash", str(WRAPPER)] + ([mode] if mode else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=world["env"], cwd=str(REPO))


def docker_calls(world):
    return world["log"].read_text().splitlines() if world["log"].exists() else []


def pin(world, value=PIN, armed=None):
    (world["base"] / "CURRENT_PAPER_SHA").write_text(value + "\n")
    if armed is not None:
        (world["base"] / "PAPER_TRACKER_ARMED").write_text(armed + "\n")


# ---------------------------------------------------------------------------------------------------------------------------------- refusals before anything starts
@linux_only
@pytest.mark.parametrize("mode", [None, "run", "dry-run"])
def test_a_missing_or_malformed_pin_is_refused_before_any_container(world, mode):
    r = run(world, mode)
    assert r.returncode == 5 and "no image pinned" in r.stderr and docker_calls(world) == []
    pin(world, "latest")
    r = run(world, mode)
    assert r.returncode == 5 and "not a 12-hex" in r.stderr and docker_calls(world) == []


@linux_only
def test_run_needs_its_own_arming_file_with_exactly_the_pin(world):
    pin(world)
    r = run(world, "run")
    assert r.returncode == 5 and "not armed" in r.stderr and docker_calls(world) == []
    pin(world, armed="b" * 12)                                                   # armed for a different image: re-pinning disarms
    r = run(world, "run")
    assert r.returncode == 5 and "armed for a different image" in r.stderr and docker_calls(world) == []


@linux_only
def test_the_collectors_pin_and_arming_file_do_not_arm_the_paper_tracker(world):
    pin(world)
    (world["base"] / "CURRENT_MECHANISM_SHA").write_text(PIN + "\n")
    (world["base"] / "FORWARD_COLLECTION_ARMED").write_text(PIN + "\n")
    r = run(world, "run")
    assert r.returncode == 5 and docker_calls(world) == []


@linux_only
def test_an_unknown_mode_is_refused(world):
    pin(world, armed=PIN)
    assert run(world, "backfill").returncode == 5 and docker_calls(world) == []


# ---------------------------------------------------------------------------------------------------------------------------------- what it runs
@linux_only
def test_an_armed_run_executes_exactly_the_next_session_at_the_papers_own_pin(world):
    pin(world, armed=PIN)
    r = run(world, "run")
    assert r.returncode == 0, r.stderr
    calls = docker_calls(world)
    args = [c for c in calls if c.startswith("ARGS:")]
    assert len(args) == 1 and "run --rm" in args[0] and "channel-sender -m research.paper_runner run --next" in args[0]
    assert f"--code-ref {PIN}" in args[0] and "--confirm-production trading_production" in args[0] and "--arming-file /armed/PAPER_TRACKER_ARMED" in args[0] and ":/armed/PAPER_TRACKER_ARMED:ro" in args[0]
    assert f"ENV: IMAGE_TAG={PIN}" in calls and " up " not in args[0]
    assert "paper-tracker run finished: exit 0" in (world["base"] / "logs" / "paper_tracker_runs.log").read_text()


@linux_only
def test_dry_run_is_read_only_needs_no_arming_and_never_mounts_the_arming_file(world):
    pin(world)
    r = run(world, "dry-run")
    assert r.returncode == 0, r.stderr
    args = [c for c in docker_calls(world) if c.startswith("ARGS:")][0]
    assert "paper_runner dry-run --next" in args and "--confirm-production" not in args and "/armed" not in args


@linux_only
@pytest.mark.parametrize("rc", [0, 2, 4, 6, 9])
def test_the_runners_exit_status_is_passed_through_and_logged(world, rc):
    pin(world, armed=PIN)
    world["exit"].write_text(str(rc))
    r = run(world, "run")
    assert r.returncode == rc
    assert f"finished: exit {rc}" in (world["base"] / "logs" / "paper_tracker_runs.log").read_text()


@linux_only
def test_a_second_run_while_one_holds_the_lock_exits_3_without_starting_a_container(world):
    import fcntl
    pin(world, armed=PIN)
    lock = open(world["base"] / ".paper_tracker.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = run(world, "run")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    assert r.returncode == 3 and docker_calls(world) == [] and "SKIP(locked)" in (world["base"] / "logs" / "paper_tracker_runs.log").read_text()


# ---------------------------------------------------------------------------------------------------------------------------------- static
def test_the_wrapper_never_touches_the_collectors_or_the_backends_pins_and_only_runs_one_off_containers():
    code = "\n".join(l for l in text(WRAPPER).splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("CURRENT_MECHANISM_SHA", "FORWARD_COLLECTION_ARMED", "CURRENT_SHA", "CURRENT_BOT_SHA", "mechanism_image_tag", "up -d", "docker rm", "systemctl", "psql"):
        assert forbidden not in code, forbidden
    assert re.search(r"dc run --rm", code) and "CURRENT_PAPER_SHA" in code and "PAPER_TRACKER_ARMED" in code


def test_the_proposed_unit_and_timer_use_the_wrapper_and_are_referenced_by_nothing_installed():
    unit = text(VPS / "donchian-paper-tracker.service.proposed")
    code = "\n".join(l for l in unit.splitlines() if not l.startswith("#"))
    assert "ExecStart=/opt/donchian/scripts/run_paper_tracker.sh run" in code and "SuccessExitStatus=2 3 6" in code and "EnvironmentFile" not in code
    timer = "\n".join(l for l in text(VPS / "donchian-paper-tracker.timer.proposed").splitlines() if not l.startswith("#"))
    assert "Persistent=false" in timer and timer.count("OnCalendar=") == 2
    for f in list(VPS.glob("*.service")) + list(VPS.glob("*.timer")) + [REPO / ".github" / "workflows" / "cd.yml", VPS / "deploy.sh"]:
        t = text(f)
        assert "paper-tracker" not in t and "run_paper_tracker" not in t and "paper_runner" not in t, f.name
