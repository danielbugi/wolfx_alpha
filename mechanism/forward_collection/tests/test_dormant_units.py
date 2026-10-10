"""Proof that COMMITTING the collector's scheduler units cannot activate production by itself.

Four independent reasons, each pinned here:
  1. nothing in the repository installs, enables or starts a systemd unit (CI/CD can only roll out the backend/mechanism images and compose files);
  2. the collector's units stand alone: no other unit orders itself after, wants, requires or is triggered by them, and the timer is the only file with an [Install] section;
  3. the wrapper the service runs refuses (exit 5) before touching a container or a database unless an explicit ARMING FILE, created by hand, names the pinned image;
  4. the units match the contract's scheduler design, so what an administrator would install is what the preflight validated.
The behaviour of an installed-but-unarmed wrapper is run for real (bash against a scratch directory with a `docker` stub that fails the test if it is ever called)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path, like every test in this directory)
from forward_collection import activation as A
from forward_collection import contract as C

REPO = Path(__file__).resolve().parents[3]
VPS = REPO / "deploy" / "vps"
WRAPPER = VPS / "run_forward_collection.sh"
SERVICE, TIMER, ALERT = VPS / A.UNIT_SERVICE, VPS / A.UNIT_TIMER, VPS / A.UNIT_ALERT
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", ".venv", "venv"}
INSTALLERS = re.compile(r"systemctl|/etc/systemd|daemon-reload|systemd-run|enable --now|\.preset", re.I)


def files(root: Path, suffixes=None):
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.parts) & SKIP_DIRS) and (suffixes is None or p.suffix in suffixes):
            yield p


def directives(path: Path):
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


# ------------------------------------------------------------------ 1. nothing installs, enables or starts a unit
def test_no_script_workflow_or_deploy_tool_installs_enables_or_starts_a_unit():
    offenders = []
    for top in ("deploy", ".github"):
        for p in files(REPO / top):
            if p.suffix in (".service", ".timer", ".proposed", ".md", ".ps1", ".sql") or p.name == "README.md":
                continue                                           # unit files only carry the words in comments (pinned below); docs describe manual steps
            if INSTALLERS.search(p.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(p.relative_to(REPO).as_posix())
    assert offenders == [], offenders


def test_the_unit_files_themselves_contain_no_installer_command_outside_comments():
    for p in list(VPS.glob("*.service")) + list(VPS.glob("*.timer")):
        for line in directives(p):
            assert not INSTALLERS.search(line), (p.name, line)


def test_the_cd_path_only_rolls_out_images_and_compose_files_never_units_or_scripts():
    ci_entry = (VPS / "ci-entry.sh").read_text(encoding="utf-8")
    assert "deploy backend" in ci_entry and "exec /opt/donchian/scripts/deploy.sh backend" in ci_entry        # the forced command accepts exactly one operation
    for line in (VPS / "deploy.sh").read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*install\s", line):
            assert "$COMPOSE_DIR/" in line, line                                                             # it only ever writes the compose directory
    cd = (REPO / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
    assert "forward" not in cd.lower() and "forward-collection" not in cd


# ------------------------------------------------------------------ 2. the units stand alone
def test_no_other_unit_orders_wants_requires_or_is_triggered_by_the_collector():
    ordering = re.compile(r"^(Wants|Requires|Requisite|BindsTo|PartOf|After|Before|Also|OnSuccess|OnFailure|Upholds|Unit|Conflicts)=", re.M)
    approved = {A.UNIT_SERVICE, A.UNIT_TIMER, A.UNIT_ALERT}
    for d in (VPS, REPO / "deploy" / "db"):
        for p in list(d.glob("*.service")) + list(d.glob("*.timer")):
            if p.name in approved:
                continue
            text = p.read_text(encoding="utf-8")
            for m in ordering.finditer(text):
                line = text[m.start():text.index("\n", m.start())] if "\n" in text[m.start():] else text[m.start():]
                assert "forward-collection" not in line, (p.name, line)


def test_the_collectors_own_units_declare_only_the_ordering_they_need():
    assert [d for d in directives(SERVICE) if re.match(r"(Wants|Requires|After|Before|OnFailure|OnSuccess|BindsTo|PartOf|Also|Upholds)=", d)] == \
        ["After=docker.service", "Requires=docker.service", "OnFailure=donchian-forward-collection-alert.service"]
    assert not [d for d in directives(TIMER) if re.match(r"(Wants|Requires|After|Before|Unit|OnSuccess|Also)=", d)]    # the timer triggers the same-named service only
    assert directives(ALERT)[-1] == "ExecStart=/bin/true"                                                           # journald-only: no channel, no network, no database


def test_only_the_timer_can_be_enabled_and_nothing_wants_the_service():
    for p in (SERVICE, ALERT):
        assert "[Install]" not in p.read_text(encoding="utf-8"), p.name               # a service with no [Install] cannot be `enable`d at all
    assert "[Install]" in TIMER.read_text(encoding="utf-8") and "WantedBy=timers.target" in directives(TIMER)
    assert not list(REPO.rglob("*.preset"))                                          # no systemd preset can enable anything on install


# ------------------------------------------------------------------ 3. the wrapper is armed by hand, never by the repository
def bash_works():
    exe = shutil.which("bash")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if r.returncode == 0 and r.stdout.strip() == "ok" else None


BASH = bash_works()
needs_bash = pytest.mark.skipif(BASH is None, reason="no working bash here")


def run_wrapper(base: Path, stub_log: Path):
    stub = base / "bin"
    stub.mkdir(exist_ok=True)
    docker = stub / "docker"
    docker.write_text("#!/bin/sh\necho \"docker $*\" >> \"" + stub_log.as_posix() + "\"\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    env = dict(os.environ, DONCHIAN_BASE=base.as_posix(), PATH=stub.as_posix() + os.pathsep + os.environ.get("PATH", ""))
    return subprocess.run([BASH, WRAPPER.as_posix()], capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def base(tmp_path):
    (tmp_path / "compose").mkdir()
    return tmp_path


@needs_bash
def test_an_installed_wrapper_with_no_pin_refuses_before_touching_anything(base, tmp_path):
    log = tmp_path / "docker.log"
    r = run_wrapper(base, log)
    assert r.returncode == 5 and "not yet pinned" in r.stderr and not log.exists()


@needs_bash
def test_a_pinned_but_unarmed_wrapper_refuses_with_exit_5_and_never_calls_docker(base, tmp_path):
    (base / "CURRENT_MECHANISM_SHA").write_text("0123456789ab\n")
    log = tmp_path / "docker.log"
    r = run_wrapper(base, log)
    assert r.returncode == 5 and "not armed" in r.stderr and not log.exists()
    assert not (base / "logs").exists() and not (base / ".forward_collection.lock").exists()          # it did not even create its log or its lock


@needs_bash
@pytest.mark.parametrize("armed", ["", "ffffffffffff", "0123456789ab extra", "0123456789AB"])
def test_a_wrapper_armed_for_a_different_image_refuses(base, tmp_path, armed):
    (base / "CURRENT_MECHANISM_SHA").write_text("0123456789ab\n")
    (base / "FORWARD_COLLECTION_ARMED").write_text(armed)
    log = tmp_path / "docker.log"
    r = run_wrapper(base, log)
    assert r.returncode == 5 and "different image" in r.stderr and not log.exists()


@needs_bash
def test_a_malformed_pin_refuses_even_if_an_arming_file_exists(base, tmp_path):
    (base / "CURRENT_MECHANISM_SHA").write_text("latest\n")
    (base / "FORWARD_COLLECTION_ARMED").write_text("latest")
    log = tmp_path / "docker.log"
    r = run_wrapper(base, log)
    assert r.returncode == 5 and "not a 12-hex sha" in r.stderr and not log.exists()


@needs_bash
@pytest.mark.skipif(sys.platform == "win32" or shutil.which("flock") is None, reason="needs Linux flock")
def test_only_a_wrapper_armed_for_exactly_the_pinned_image_reaches_docker_and_it_passes_the_pin_to_the_collector(base, tmp_path):
    (base / "CURRENT_MECHANISM_SHA").write_text("0123456789ab\n")
    (base / "FORWARD_COLLECTION_ARMED").write_text("0123456789ab")
    log = tmp_path / "docker.log"
    r = run_wrapper(base, log)
    assert r.returncode == 0, r.stderr
    called = log.read_text()
    assert "forward_collection run --latest-completed --apply --with-sector-history-check --with-capture-check --code-ref 0123456789ab" in called


def test_the_arming_file_is_never_committed_or_created_by_any_repository_file():
    assert not list(REPO.rglob("FORWARD_COLLECTION_ARMED"))
    creators = []
    for p in files(REPO / "deploy"):
        if p.suffix in (".md",) or p == WRAPPER:
            continue
        if "FORWARD_COLLECTION_ARMED" in p.read_text(encoding="utf-8", errors="ignore"):
            creators.append(p.relative_to(REPO).as_posix())
    assert creators == []


# ------------------------------------------------------------------ 4. what would be installed is what the preflight validated
def test_the_committed_units_pass_the_preflights_dormancy_check():
    ok, detail = A.scheduler_units_check(REPO)
    assert ok, detail["problems"]
    assert detail["committed"] == sorted(A.UNIT_FILES + A.SCAN_FILES)


def test_the_timer_fires_exactly_when_the_contract_design_says():
    fires = A.ONCALENDAR.findall(TIMER.read_text(encoding="utf-8"))
    assert fires == [(h, C.SCHEDULER_DESIGN["timezone"]) for h in C.SCHEDULER_DESIGN["fires_local"]]
    assert "Persistent=true" in directives(TIMER)


def test_the_service_has_the_contracts_timeout_and_treats_a_lock_refusal_as_not_a_failure():
    d = directives(SERVICE)
    assert f"TimeoutStartSec={C.SCHEDULER_DESIGN['timeout_minutes']}m" in d and "SuccessExitStatus=3" in d and "Type=oneshot" in d


def test_the_preflight_blocks_if_the_units_are_missing_or_stop_being_dormant(tmp_path):
    ok, detail = A.scheduler_units_check(tmp_path)
    assert not ok and len(detail["problems"]) == len(A.UNIT_FILES) + len(A.SCAN_FILES)
    vps = tmp_path / "deploy" / "vps"
    vps.mkdir(parents=True)
    for name in A.UNIT_FILES + A.SCAN_FILES:
        shutil.copy(VPS / name, vps / name)
    assert A.scheduler_units_check(tmp_path)[0]
    (vps / A.UNIT_WRAPPER).write_text((VPS / A.UNIT_WRAPPER).read_text(encoding="utf-8").replace("FORWARD_COLLECTION_ARMED", "X"), encoding="utf-8")
    ok, detail = A.scheduler_units_check(tmp_path)
    assert not ok and any("arming" in p for p in detail["problems"])
    shutil.copy(VPS / A.UNIT_WRAPPER, vps / A.UNIT_WRAPPER)
    (vps / "donchian-pipeline.service").write_text("[Unit]\nAfter=donchian-forward-collection.service\n", encoding="utf-8")
    ok, detail = A.scheduler_units_check(tmp_path)
    assert not ok and any("another unit references the collector" in p for p in detail["problems"])
