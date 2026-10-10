"""R2 coordination + R3 timing: the scan wrapper, its (uninstalled) units, and the schedule they form with the post-market retry and the collector.

Proven here:
  * the wrapper refuses outside the safe window (before the final retry's scheduled start, or when the next night's retries have begun), takes the retry lock and the
    pipeline lock IN THAT ORDER, holds both for the whole scan, runs exactly `--scan-only --session latest-completed` (never a rebuild, never a loop) and passes the
    scan's exit status through;
  * with a REAL flock (Linux only): a running retry makes it wait, then proceed; a retry that outlasts the wait bound makes it refuse (nothing scanned); a second
    scan is refused; and while it runs no one else can take the retry lock;
  * the units stand alone, are never installed by the repository, the timer fires once a night and cannot loop;
  * the schedule is ordered correctly in every daylight-saving regime (summer / winter / both mismatch windows): scan after the final retry's start and its
    worst-case end, collector first fire after the scan plus its budget, both fires before the session's knowledge cutoff."""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
from forward_collection import activation as A
from forward_collection import contract as C
from market_intelligence import inputs

REPO = Path(__file__).resolve().parents[3]
VPS = REPO / "deploy" / "vps"
WRAPPER = VPS / A.SCAN_WRAPPER
SERVICE, TIMER = VPS / A.SCAN_SERVICE, VPS / A.SCAN_TIMER
JER, NY, UTC = ZoneInfo("Asia/Jerusalem"), ZoneInfo("America/New_York"), timezone.utc
REGIMES = [("summer (both on DST)", date(2026, 7, 6)), ("winter (neither on DST)", date(2026, 12, 7)),
           ("spring mismatch: US on DST, Israel not yet", date(2026, 3, 16)), ("autumn mismatch: Israel off DST, US still on", date(2026, 10, 26))]


def directives(path: Path):
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


# ------------------------------------------------------------------ the units
def test_the_scan_units_stand_alone_and_are_never_installed_by_the_repository():
    assert [d for d in directives(SERVICE) if re.match(r"(Wants|Requires|After|Before|OnFailure|OnSuccess|BindsTo|PartOf|Also|Upholds)=", d)] == ["After=docker.service", "Requires=docker.service"]
    assert "[Install]" not in SERVICE.read_text(encoding="utf-8") and "WantedBy=timers.target" in directives(TIMER)
    for p in VPS.glob("*.service"):
        if p.name != A.SCAN_SERVICE:
            assert "discontinuity-scan" not in p.read_text(encoding="utf-8"), p.name            # nothing orders itself after, wants or triggers the scan
    for p in (VPS / A.UNIT_SERVICE, VPS / A.UNIT_TIMER, VPS / A.UNIT_ALERT):
        assert not [d for d in directives(p) if "discontinuity" in d], p.name                                  # the collector's units do not run, order or trigger the scan
    assert "run_discontinuity_scan" not in chr(10).join(ln for ln in (VPS / A.UNIT_WRAPPER).read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
    for p in (SERVICE, TIMER, WRAPPER):
        assert "forward-collection" not in p.read_text(encoding="utf-8"), p.name


def test_the_timer_fires_once_a_night_at_the_contract_time_and_can_neither_loop_nor_catch_up():
    d = directives(TIMER)
    assert A.ONCALENDAR.findall("\n".join(d)) == [("06:20", C.SCHEDULER_DESIGN["timezone"])] and C.SCHEDULER_DESIGN["scan_local"] == "06:20"
    assert "Persistent=false" in d
    assert not [x for x in d if re.match(r"(OnUnitActiveSec|OnUnitInactiveSec|OnBootSec|OnActiveSec|OnStartupSec)=", x)]       # no repeat: no recurring rescan loop
    assert [x for x in d if x.startswith("OnCalendar=")] == ["OnCalendar=*-*-* 06:20:00 Asia/Jerusalem"]
    svc = directives(SERVICE)
    assert "ExecStart=/opt/donchian/scripts/run_discontinuity_scan.sh" in svc and "Type=oneshot" in svc and not [x for x in svc if x.startswith("SuccessExitStatus")]


def test_the_wrapper_text_never_rebuilds_applies_loops_or_releases_a_lock():
    code = "\n".join(ln for ln in WRAPPER.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
    assert "--scan-only --session latest-completed" in code
    for forbidden in ("--replace", "--apply", "while ", "until ", "sleep ", "9>&-", "8>&-", "7>&-", "systemctl"):
        assert forbidden not in code, forbidden
    order = [code.index(x) for x in ('exec 7>"$BASE/.discontinuity_scan.lock"', 'exec 9>"$BASE/.postmarket_retry.lock"', 'exec 8>"$BASE/.pipeline.lock"')]
    assert order == sorted(order)                                                                 # own lock, then the retry lock, then the pipeline lock
    assert code.index("flock -w") > code.index('exec 9>') and code.index('--entrypoint python pipeline') > code.index('exec 8>')


def test_the_preflights_unit_check_validates_the_scan_files_too(tmp_path):
    ok, detail = A.scheduler_units_check(REPO)
    assert ok, detail["problems"]
    assert {A.SCAN_SERVICE, A.SCAN_TIMER, A.SCAN_WRAPPER} <= set(detail["committed"])
    vps = tmp_path / "deploy" / "vps"
    vps.mkdir(parents=True)
    for name in A.UNIT_FILES + A.SCAN_FILES:
        shutil.copy(VPS / name, vps / name)
    assert A.scheduler_units_check(tmp_path)[0]
    cases = [(A.SCAN_TIMER, lambda t: t.replace("Persistent=false", "Persistent=true"), "Persistent=false"),
             (A.SCAN_TIMER, lambda t: t + "\nOnUnitActiveSec=10min\n", "rescan loop"),
             (A.SCAN_TIMER, lambda t: t.replace("06:20:00", "05:30:00"), "single scan time"),
             (A.SCAN_WRAPPER, lambda t: t.replace("--scan-only", "--replace"), "scan-only mode"),
             (A.SCAN_WRAPPER, lambda t: t.replace(".postmarket_retry.lock", ".other.lock"), "retry lock"),
             (A.SCAN_WRAPPER, lambda t: t.replace(".pipeline.lock", ".other2.lock"), "pipeline lock"),
             (A.SCAN_SERVICE, lambda t: t + "\nSuccessExitStatus=1\n", "failed unit"),
             (A.SCAN_SERVICE, lambda t: t + "\n# After=donchian-forward-collection.service\n", "stand alone")]
    for name, edit, needle in cases:
        original = (VPS / name).read_text(encoding="utf-8")
        (vps / name).write_text(edit(original), encoding="utf-8")
        ok, detail = A.scheduler_units_check(tmp_path)
        assert not ok and any(needle in p for p in detail["problems"]), (name, needle, detail["problems"])
        shutil.copy(VPS / name, vps / name)
    assert A.scheduler_units_check(tmp_path)[0]


# ------------------------------------------------------------------ the schedule across every daylight-saving regime
@pytest.mark.parametrize("label,session", REGIMES)
def test_the_scan_follows_the_final_retry_and_precedes_the_collector_in_every_regime(label, session):
    D = C.SCHEDULER_DESIGN
    retry, scan = C.last_retry_start_utc(session), C.scan_instant_utc(session)
    first, last = C.fire_instants_utc(session)
    cutoff = inputs.knowledge_cutoff(session)
    assert retry.astimezone(JER).strftime("%H:%M") == "06:00" and scan.astimezone(JER).strftime("%H:%M") == "06:20" and first.astimezone(JER).strftime("%H:%M") == "06:45"
    assert scan - retry == timedelta(minutes=20) >= timedelta(minutes=D["retry_max_runtime_minutes"])         # after the final retry's start AND its worst-case end
    assert first - scan >= timedelta(minutes=D["scan_budget_minutes"])                                        # the scan (wait + run) is over before the first fire
    assert retry.astimezone(JER).date() == scan.astimezone(JER).date() == first.astimezone(JER).date() == session + timedelta(days=1)        # the calendar day AFTER the session
    assert last < cutoff and first < cutoff                                                                    # both collector fires are inside the operational window


def test_in_utc_the_scan_is_03_20_in_israeli_summer_time_and_04_20_in_winter_and_the_first_fire_03_45_and_04_45():
    s, w = C.scan_instant_utc(date(2026, 7, 6)), C.scan_instant_utc(date(2026, 12, 7))
    assert (s.hour, s.minute) == (3, 20) and (w.hour, w.minute) == (4, 20)
    f_s, f_w = C.fire_instants_utc(date(2026, 7, 6))[0], C.fire_instants_utc(date(2026, 12, 7))[0]
    assert (f_s.hour, f_s.minute) == (3, 45) and (f_w.hour, f_w.minute) == (4, 45)
    for session in (date(2026, 3, 16), date(2026, 10, 26)):                                                    # the Israel/US mismatch windows keep the Israeli ordering
        assert C.scan_instant_utc(session) - C.last_retry_start_utc(session) == timedelta(minutes=20)


def test_the_design_validator_rejects_a_scan_that_could_race_the_retry_or_a_collector_that_could_beat_the_scan():
    base = dict(C.SCHEDULER_DESIGN)
    assert C.validate_design(1, base) == [] and A.scan_schedule_problems() == []
    assert any("after the final post-market retry" in p for p in C.validate_design(1, dict(base, scan_local="05:59")))
    assert any("worst-case end" in p for p in C.validate_design(1, dict(base, scan_local="06:10")))
    assert any("wait-and-run budget" in p for p in C.validate_design(1, dict(base, fires_local=("06:30", "08:15"))))
    assert any("missing" in p for p in C.validate_design(1, {k: v for k, v in base.items() if k != "scan_local"}))


def test_every_collector_fire_stays_before_the_decision_deadline_with_one_grace_day():
    assert C.required_grace_days() == 1
    d = date(2026, 1, 1)
    while d.year == 2026:
        if d.weekday() < 5:
            assert C.scan_instant_utc(d) < C.fire_instants_utc(d)[0] < C.fire_instants_utc(d)[1], d
        d += timedelta(days=1)


# ------------------------------------------------------------------ the wrapper, run for real against a scratch directory
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
REAL_FLOCK = shutil.which("flock") if sys.platform != "win32" else None
needs_flock = pytest.mark.skipif(REAL_FLOCK is None, reason="needs Linux flock")

SHIM_FLOCK = r'''#!/bin/sh
# records every call; fails the lock whose fd number is listed in SHIM_FLOCK_FAIL_FD
echo "flock $*" >> "$SHIM_LOG"
for a; do last=$a; done
case " $SHIM_FLOCK_FAIL_FD " in *" $last "*) exit 1 ;; esac
exit 0
'''


def make_base(tmp_path, pin="0123456789ab", docker_rc=0, docker_sleep=0):
    base = tmp_path / "base"
    (base / "compose").mkdir(parents=True)
    (base / "logs").mkdir()
    if pin is not None:
        (base / "CURRENT_MECHANISM_SHA").write_text(pin + "\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "calls.log"
    docker = stub / "docker"
    docker.write_text(f"#!/bin/sh\necho \"docker $*\" >> \"{log.as_posix()}\"\n[ {docker_sleep} -gt 0 ] && sleep {docker_sleep}\nexit {docker_rc}\n", encoding="utf-8")
    docker.chmod(0o755)
    return base, stub, log


def run_wrapper(base, stub, log, *, now_local="06:30", shim_fail="", real_flock=False, extra_env=None, timeout=60):
    stub = Path(stub)
    if not real_flock:
        shim = stub / "flock"
        shim.write_text(SHIM_FLOCK, encoding="utf-8")
        shim.chmod(0o755)
    env = dict(os.environ, DONCHIAN_BASE=base.as_posix(), PATH=stub.as_posix() + os.pathsep + os.environ.get("PATH", ""), SCAN_NOW_LOCAL=now_local,
               SHIM_LOG=log.as_posix(), SHIM_FLOCK_FAIL_FD=shim_fail)
    env.update(extra_env or {})
    return subprocess.run([BASH, WRAPPER.as_posix()], capture_output=True, text=True, env=env, timeout=timeout)


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


@needs_bash
@pytest.mark.parametrize("now", ["00:00", "05:59", "06:00", "23:45", "23:59"])
def test_it_refuses_inside_the_retry_window_and_never_reaches_docker(tmp_path, now):
    base, stub, log = make_base(tmp_path)
    r = run_wrapper(base, stub, log, now_local=now)
    assert r.returncode == 6 and "REFUSED(retry window)" in (r.stdout + r.stderr) and not any(c.startswith("docker") for c in calls(log))
    assert not any(c.startswith("flock") for c in calls(log))                                                    # not even a lock was taken


@needs_bash
@pytest.mark.parametrize("now", ["06:01", "06:20", "12:00", "23:44"])
def test_it_runs_the_scan_only_command_outside_the_retry_window(tmp_path, now):
    base, stub, log = make_base(tmp_path)
    r = run_wrapper(base, stub, log, now_local=now)
    assert r.returncode == 0, r.stdout + r.stderr
    docker_calls = [c for c in calls(log) if c.startswith("docker")]
    assert len(docker_calls) == 1
    assert "run --rm --entrypoint python pipeline ml_training/data_preparation/build_dataset.py --scan-only --session latest-completed" in docker_calls[0]
    assert "--replace" not in docker_calls[0] and "IMAGE_TAG" not in docker_calls[0]


@needs_bash
def test_it_takes_its_own_lock_then_the_retry_lock_then_the_pipeline_lock_before_docker(tmp_path):
    base, stub, log = make_base(tmp_path)
    assert run_wrapper(base, stub, log).returncode == 0
    seq = calls(log)
    assert [c.split()[1:] for c in seq if c.startswith("flock")] == [["-n", "7"], ["-w", "1020", "9"], ["-w", "300", "8"]]
    assert max(i for i, c in enumerate(seq) if c.startswith("flock")) < min(i for i, c in enumerate(seq) if c.startswith("docker"))


@needs_bash
@pytest.mark.parametrize("fail_fd,rc,text", [("9", 7, "post-market retry lock was not free"), ("8", 7, "pipeline lock was not free"), ("7", 8, "another discontinuity scan")])
def test_a_lock_it_cannot_get_means_nothing_is_scanned(tmp_path, fail_fd, rc, text):
    base, stub, log = make_base(tmp_path)
    r = run_wrapper(base, stub, log, shim_fail=fail_fd)
    assert r.returncode == rc and text in (r.stdout + r.stderr) and not any(c.startswith("docker") for c in calls(log))


@needs_bash
@pytest.mark.parametrize("pin", [None, "latest", "0123456789AB"])
def test_without_a_valid_pin_it_is_a_setup_error(tmp_path, pin):
    base, stub, log = make_base(tmp_path, pin=pin)
    r = run_wrapper(base, stub, log)
    assert r.returncode == 9 and not any(c.startswith("docker") for c in calls(log))


@needs_bash
@pytest.mark.parametrize("rc", [1, 3, 5])
def test_the_scans_exit_status_passes_through_so_a_failed_scan_is_a_failed_unit(tmp_path, rc):
    base, stub, log = make_base(tmp_path, docker_rc=rc)
    r = run_wrapper(base, stub, log)
    assert r.returncode == rc and f"exit {rc}" in (base / "logs" / "discontinuity_scan_runs.log").read_text()


# ------------------------------------------------------------------ real flock (Linux): coordination with a running retry
def hold(lockfile: Path, seconds: float):
    p = subprocess.Popen([REAL_FLOCK, "-x", lockfile.as_posix(), "sleep", str(seconds)])
    for _ in range(100):
        time.sleep(0.05)
        if subprocess.run([REAL_FLOCK, "-n", lockfile.as_posix(), "true"]).returncode != 0:      # the holder has the lock
            return p
    p.kill()
    raise AssertionError("could not hold the lock")


@needs_bash
@needs_flock
def test_a_running_retry_makes_it_wait_and_it_proceeds_after_the_retry_finishes(tmp_path):
    base, stub, log = make_base(tmp_path)
    (base / ".postmarket_retry.lock").touch()
    holder = hold(base / ".postmarket_retry.lock", 2)
    t0 = time.time()
    r = run_wrapper(base, stub, log, real_flock=True, extra_env={"SCAN_RETRY_WAIT_SECONDS": "20"})
    holder.wait()
    assert r.returncode == 0, r.stdout + r.stderr
    assert time.time() - t0 >= 1.0 and sum(c.startswith("docker") for c in calls(log)) == 1


@needs_bash
@needs_flock
def test_a_retry_that_outlasts_the_wait_bound_means_nothing_is_scanned(tmp_path):
    base, stub, log = make_base(tmp_path)
    (base / ".postmarket_retry.lock").touch()
    holder = hold(base / ".postmarket_retry.lock", 4)
    r = run_wrapper(base, stub, log, real_flock=True, extra_env={"SCAN_RETRY_WAIT_SECONDS": "1"})
    holder.kill()
    assert r.returncode == 7 and not any(c.startswith("docker") for c in calls(log))


@needs_bash
@needs_flock
def test_a_second_scan_is_refused_while_one_is_running(tmp_path):
    base, stub, log = make_base(tmp_path)
    (base / ".discontinuity_scan.lock").touch()
    holder = hold(base / ".discontinuity_scan.lock", 3)
    r = run_wrapper(base, stub, log, real_flock=True)
    holder.kill()
    assert r.returncode == 8 and not any(c.startswith("docker") for c in calls(log))


@needs_bash
@needs_flock
def test_while_the_scan_runs_no_retry_or_pipeline_can_take_its_lock(tmp_path):
    base, stub, log = make_base(tmp_path, docker_sleep=3)
    out = {}

    def go():
        out["r"] = run_wrapper(base, stub, log, real_flock=True)
    th = threading.Thread(target=go)
    th.start()
    deadline = time.time() + 20
    while time.time() < deadline and not any(c.startswith("docker") for c in calls(log)):          # the scan (docker stub) is now running
        time.sleep(0.05)
    assert any(c.startswith("docker") for c in calls(log))
    for lock in (".postmarket_retry.lock", ".pipeline.lock"):
        busy = subprocess.run([REAL_FLOCK, "-n", (base / lock).as_posix(), "true"]).returncode          # what the retry wrapper itself does: flock -n
        assert busy != 0, f"{lock} was free while the scan was running"
    th.join()
    assert out["r"].returncode == 0
    for lock in (".postmarket_retry.lock", ".pipeline.lock", ".discontinuity_scan.lock"):
        assert subprocess.run([REAL_FLOCK, "-n", (base / lock).as_posix(), "true"]).returncode == 0       # all released afterwards
