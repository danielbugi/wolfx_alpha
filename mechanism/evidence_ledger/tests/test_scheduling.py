"""Evidence Ledger nightly capture: the explicit-session resolution, the readiness gate, the store lock, the dormant wrapper/unit/timer and the backup leg.
No database; the wrapper behaviour tests run the real script against a stub `docker` (Linux only)."""
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from evidence_ledger import cli as C  # noqa: E402
from evidence_ledger.store import LedgerCorrupt, Store  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
VPS = REPO / "deploy" / "vps"
WRAPPER = VPS / "run_evidence_ledger.sh"
BACKUP = REPO / "deploy" / "db" / "nightly_backup.sh"
PIN = "a" * 12
UTC = timezone.utc
linux_only = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None or shutil.which("flock") is None, reason="needs POSIX bash + flock (CI / VPS)")


def text(p):
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def code_of(p):
    return "\n".join(l for l in text(p).splitlines() if not l.lstrip().startswith("#"))


# ---------------------------------------------------------------- the session is explicit, from a trustworthy calendar
class FakeCalendar:
    def __init__(self, source, latest):
        self.source, self.latest, self.asked = source, latest, None

    def get_sessions(self, today):
        self.asked = today
        return {}, self.source

    def latest_completed(self, sessions, now):
        return self.latest


@pytest.mark.parametrize("source", ["alpaca", "cache", "stale-cache"])
def test_latest_completed_comes_from_the_calendar_not_the_wall_clock(source):
    cal = FakeCalendar(source, date(2026, 11, 12))
    now = datetime(2026, 11, 13, 5, 30, tzinfo=UTC)                  # 00:30 New York on 11-13: the calendar date is NOT the session
    assert C.resolve_latest_completed(now, cal) == date(2026, 11, 12) and cal.asked == date(2026, 11, 13)


def test_the_weekday_fallback_or_an_empty_calendar_is_refused():
    with pytest.raises(ValueError, match="trustworthy"):
        C.resolve_latest_completed(datetime(2026, 11, 13, 5, tzinfo=UTC), FakeCalendar("weekday-fallback", date(2026, 11, 12)))
    with pytest.raises(ValueError, match="no completed session"):
        C.resolve_latest_completed(datetime(2026, 11, 13, 5, tzinfo=UTC), FakeCalendar("alpaca", None))


@pytest.mark.parametrize("bench,stocks,ready", [(1, 3000, True), (0, 3000, False), (1, 0, False), (1, C.MIN_STOCK_BARS - 1, False), (1, C.MIN_STOCK_BARS, True)])
def test_capture_waits_until_the_sessions_prices_are_loaded(monkeypatch, bench, stocks, ready):
    monkeypatch.setattr(C, "_rows", lambda conn, sql, params: [{"bench": bench, "stocks": stocks}])
    if ready:
        assert C.check_ready(None, date(2026, 11, 12)) == {"bench": bench, "stocks": stocks}
    else:
        with pytest.raises(C.NotReady):
            C.check_ready(None, date(2026, 11, 12))


def test_the_cli_takes_exactly_one_of_session_or_latest_completed():
    for argv in (["capture", "--store", "x"], ["capture", "--session", "2026-11-12", "--latest-completed", "--store", "x"]):
        with pytest.raises(SystemExit):
            C.main(argv)


def test_a_refused_calendar_exits_5_before_any_database_or_file_access(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "resolve_latest_completed", lambda now: (_ for _ in ()).throw(ValueError("no trustworthy market calendar")))
    store = tmp_path / "l.jsonl"
    assert C.main(["capture", "--latest-completed", "--store", str(store)]) == C.EXIT_REFUSED
    assert not store.exists() and not (tmp_path / "l.jsonl.lock").exists()


# ---------------------------------------------------------------- the store
def test_a_half_written_last_line_is_corrupt_not_a_crash(tmp_path):
    p = tmp_path / "l.jsonl"
    s = Store(str(p))
    s.append("observation", "A", {"x": 1})
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "kind": "obs')
    with pytest.raises(LedgerCorrupt, match="unreadable line 2"):
        Store(str(p))


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl")
def test_the_store_lock_is_exclusive(tmp_path):
    p = str(tmp_path / "l.jsonl")
    first = C.store_lock(p)
    try:
        assert first and C.store_lock(p) is False
    finally:
        first.close()
    again = C.store_lock(p)
    assert again
    again.close()


# ---------------------------------------------------------------- the dormant wrapper
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
    return subprocess.run(["bash", str(WRAPPER)] + ([mode] if mode else []), capture_output=True, text=True, env=world["env"], cwd=str(REPO))


def calls(world):
    return world["log"].read_text().splitlines() if world["log"].exists() else []


def arm(world, value=PIN, armed=None, ledger_dir=True):
    (world["base"] / "CURRENT_EVIDENCE_LEDGER_SHA").write_text(value + "\n")
    if armed is not None:
        (world["base"] / "EVIDENCE_LEDGER_ARMED").write_text(armed + "\n")
    if ledger_dir:
        (world["base"] / "evidence_ledger").mkdir(exist_ok=True)


@linux_only
def test_no_pin_a_bad_pin_or_no_arming_is_refused_before_any_container(world):
    r = run(world)
    assert r.returncode == 5 and "no image pinned" in r.stderr and calls(world) == []
    arm(world, "latest")
    assert run(world).returncode == 5 and calls(world) == []
    arm(world)
    r = run(world)
    assert r.returncode == 5 and "not armed" in r.stderr and calls(world) == []
    arm(world, armed="b" * 12)
    r = run(world)
    assert r.returncode == 5 and "different image" in r.stderr and calls(world) == []


@linux_only
def test_the_mechanism_pin_and_the_collectors_arming_file_do_not_arm_the_ledger(world):
    (world["base"] / "CURRENT_MECHANISM_SHA").write_text(PIN + "\n")
    (world["base"] / "FORWARD_COLLECTION_ARMED").write_text(PIN + "\n")
    (world["base"] / "evidence_ledger").mkdir()
    assert run(world).returncode == 5 and calls(world) == []


@linux_only
def test_a_missing_ledger_directory_is_refused(world):
    arm(world, armed=PIN, ledger_dir=False)
    r = run(world)
    assert r.returncode == 5 and "evidence_ledger" in r.stderr and calls(world) == []


@linux_only
def test_an_armed_capture_runs_the_latest_completed_session_read_only_at_its_own_pin(world):
    arm(world, armed=PIN)
    r = run(world, "capture")
    assert r.returncode == 0, r.stderr
    c = calls(world)
    args = [x for x in c if x.startswith("ARGS:")]
    assert len(args) == 1 and "run --rm" in args[0] and " up " not in args[0]
    assert "channel-sender mechanism/evidence_ledger/cli.py capture --latest-completed --store /ledger/ledger.jsonl" in args[0]
    assert "default_transaction_read_only=on" in args[0] and f"{world['base']}/evidence_ledger:/ledger" in args[0]
    assert f"ENV: IMAGE_TAG={PIN}" in c
    assert "evidence-ledger capture finished: exit 0" in (world["base"] / "logs" / "evidence_ledger_runs.log").read_text()


@linux_only
def test_report_needs_no_arming_and_mounts_the_ledger_read_only(world):
    arm(world)
    assert run(world, "report").returncode == 0
    args = [x for x in calls(world) if x.startswith("ARGS:")][0]
    assert "cli.py report" in args and "/ledger:ro" in args


@linux_only
@pytest.mark.parametrize("rc", [0, 2, 5, 8, 9])
def test_the_clis_exit_status_is_passed_through_and_logged(world, rc):
    arm(world, armed=PIN)
    world["exit"].write_text(str(rc))
    assert run(world).returncode == rc
    assert f"finished: exit {rc}" in (world["base"] / "logs" / "evidence_ledger_runs.log").read_text()


@linux_only
def test_a_second_run_while_one_holds_the_lock_exits_3(world):
    import fcntl
    arm(world, armed=PIN)
    lock = open(world["base"] / ".evidence_ledger_run.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = run(world)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    assert r.returncode == 3 and calls(world) == []


@linux_only
def test_an_unknown_mode_is_refused(world):
    arm(world, armed=PIN)
    assert run(world, "backfill").returncode == 5 and calls(world) == []


# ---------------------------------------------------------------- static
def test_the_wrapper_never_touches_other_pins_or_long_running_services():
    code = code_of(WRAPPER)
    for forbidden in ("CURRENT_MECHANISM_SHA", "FORWARD_COLLECTION_ARMED", "CURRENT_PAPER_SHA", "CURRENT_SHA", "CURRENT_BOT_SHA", "up -d", "docker rm", "systemctl", "psql",
                      "--session"):
        assert forbidden not in code, forbidden
    assert "dc run --rm" in code and "CURRENT_EVIDENCE_LEDGER_SHA" in code and "EVIDENCE_LEDGER_ARMED" in code and "--latest-completed" in code


def test_the_unit_and_timer_are_dormant_and_referenced_by_nothing_installed():
    unit = code_of(VPS / "donchian-evidence-ledger.service")
    assert "ExecStart=/opt/donchian/scripts/run_evidence_ledger.sh capture" in unit and "SuccessExitStatus=3" in unit and "EnvironmentFile" not in unit
    timer = code_of(VPS / "donchian-evidence-ledger.timer")
    cal = re.findall(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d):00 Asia/Jerusalem$", timer, re.MULTILINE)
    assert "Persistent=false" in timer and len(cal) == 2 and timer.count("OnCalendar=") == 2
    backup = re.findall(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d)", code_of(REPO / "deploy" / "db" / "donchian-nightly-backup.timer"), re.MULTILINE)
    assert all((int(h), int(m)) < (int(backup[0][0]), int(backup[0][1])) for h, m in cal), "both captures must finish before the nightly backup copies the file"
    for f in [REPO / ".github" / "workflows" / "cd.yml", VPS / "deploy.sh", REPO / "automation_pipeline.sh"]:
        t = text(f)
        assert "evidence_ledger" not in t and "evidence-ledger" not in t, f.name


def test_the_backup_copies_the_ledger_under_its_lock_and_never_fails_the_dump_because_of_it():
    code = code_of(BACKUP)
    block = code[code.index('LEDGER="$BASE/evidence_ledger/ledger.jsonl"'):code.index("chmod 700")]
    assert 'flock -w 600 "$LEDGER.lock" cp -p "$LEDGER" "$DIR/evidence_ledger.jsonl"' in block and "sha256sum -c" in block
    assert "fail " not in block and "exit" not in block and "WARNING" in block
    assert "$LEDGER_NOTE" in code[code.index('log "OK'):]
    assert C.store_lock.__doc__ and ".lock" in C.store_lock.__doc__          # the CLI's lock file is '<store>.lock': the same file the backup waits on
