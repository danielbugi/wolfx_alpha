"""The quarterly fundamentals updater is bounded: it stops cleanly at a wall-clock budget, leaves the unreached symbols due (stalest first, never-succeeded last), and the pipeline treats the step as
OPTIONAL with a hard timeout (a slow vendor can no longer stall the screener, the capture and the publication until the service timeout). No database and no network: fake clock, stubbed symbol update."""
import logging
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from mechanism.data_updaters import quarterly_fundamentals_updater as Q  # noqa: E402

linux_only = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None or shutil.which("timeout") is None, reason="needs POSIX bash + coreutils timeout (CI / VPS)")


class Clock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def updater(monkeypatch, results=None):
    u = object.__new__(Q.QuarterlyFundamentalsUpdater)
    u.logger = logging.getLogger("test_quarterly_budget")
    u.rate_limit_delay = 0.0
    calls = []

    def fake_update(symbol):
        calls.append(symbol)
        return (results or {}).get(symbol, True)
    monkeypatch.setattr(u, "update_symbol", fake_update)
    return u, calls


def test_without_a_budget_every_due_symbol_is_attempted(monkeypatch):
    u, calls = updater(monkeypatch)
    r = u.run_update(symbols=["A", "B", "C"], clock=Clock(100.0))
    assert calls == ["A", "B", "C"] and r["symbols_processed"] == 3 and r["symbols_due"] == 3 and r["symbols_remaining"] == 0 and r["stopped_by_budget"] is False and r["success"] is True


def test_the_budget_stops_cleanly_before_a_new_symbol_and_reports_what_is_left(monkeypatch):
    u, calls = updater(monkeypatch)
    # clock reads: t0=0, then 10, 20, 30, ... (one read per loop check); budget 25 -> checks at 10 (ok), 20 (ok), 30 (stop): exactly two symbols attempted
    r = u.run_update(symbols=list("ABCDEF"), time_budget_seconds=25, clock=Clock(10.0))
    assert calls == ["A", "B"] and r["symbols_processed"] == 2 and r["symbols_due"] == 6 and r["symbols_remaining"] == 4 and r["stopped_by_budget"] is True


def test_a_zero_budget_attempts_nothing_and_a_failing_symbol_does_not_stop_the_run(monkeypatch):
    u, calls = updater(monkeypatch)
    assert u.run_update(symbols=["A", "B"], time_budget_seconds=0, clock=Clock(1.0))["symbols_processed"] == 0 and calls == []
    u2, calls2 = updater(monkeypatch, results={"B": False})
    r = u2.run_update(symbols=["A", "B", "C"], time_budget_seconds=1000, clock=Clock(1.0))
    assert calls2 == ["A", "B", "C"] and r["symbols_failed"] == 1 and r["symbols_successful"] == 2 and r["success"] is False and r["success_rate"] == pytest.approx(200 / 3)


def test_the_due_list_is_ordered_stalest_first_with_never_succeeded_symbols_last(monkeypatch):
    seen = {}

    class FakeDb:
        @staticmethod
        def execute_dict_query(query, params):
            seen["query"], seen["params"] = query, params
            return [{"symbol": "X"}]
    monkeypatch.setattr(Q, "db", FakeDb)
    u = object.__new__(Q.QuarterlyFundamentalsUpdater)
    u.logger = logging.getLogger("test_quarterly_budget")
    assert u.get_symbols_to_update() == ["X"]
    assert re.search(r"ORDER BY \(qf\.last_checked IS NULL\), qf\.last_checked, sp\.symbol", seen["query"]) and seen["params"] == (25,)


def test_the_cli_exposes_the_budget_flag():
    src = open(Q.__file__, encoding="utf-8").read()
    assert "--time-budget-seconds" in src and "time_budget_seconds=args.time_budget_seconds" in src


# ---------------------------------------------------------------------------------------------------------------------------------- the pipeline wrapper
def _function_text():
    text = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read().replace("\r\n", "\n")
    m = re.search(r"^run_optional_step\(\) \{\n.*?^\}\n", text, flags=re.S | re.M)
    assert m, "run_optional_step is missing from automation_pipeline.sh"
    return m.group(0)


def _run(tmp_path, python_body, limit):
    py = tmp_path / "fakepython"
    py.write_text("#!/usr/bin/env bash\n" + python_body + "\n")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "t.sh"
    script.write_text("set -e\nset -o pipefail\nYELLOW=''; NC=''; TOTAL_STEPS=13\nLOG_FILE=\"" + str(tmp_path / "log.txt") + "\"\nPYTHON=\"" + str(py) + "\"\n"
                      "log() { echo \"$*\"; }\nlog_success() { echo \"SUCCESS: $1\"; }\n" + _function_text() + "\nrun_optional_step 8 \"quarterly fundamentals updater\" " + str(limit) + " --time-budget-seconds 5\necho AFTER-STEP-REACHED\n")
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=120)


@linux_only
def test_an_optional_step_that_succeeds_logs_success_and_the_pipeline_continues(tmp_path):
    r = _run(tmp_path, "echo ok-output; exit 0", 30)
    assert r.returncode == 0 and "SUCCESS: quarterly fundamentals updater completed" in r.stdout and "AFTER-STEP-REACHED" in r.stdout and "WARNING" not in r.stdout


@linux_only
def test_an_optional_step_that_fails_warns_and_the_pipeline_continues(tmp_path):
    r = _run(tmp_path, "echo boom >&2; exit 3", 30)
    assert r.returncode == 0 and "WARNING: quarterly fundamentals updater failed (exit 3)" in r.stdout and "AFTER-STEP-REACHED" in r.stdout


@linux_only
def test_an_optional_step_that_hangs_is_killed_at_its_limit_warns_and_the_pipeline_continues(tmp_path):
    r = _run(tmp_path, "sleep 60", 2)
    assert r.returncode == 0 and "hit its 2s limit" in r.stdout and "AFTER-STEP-REACHED" in r.stdout


def test_step_8_is_the_only_optional_step_and_carries_both_the_budget_and_the_hard_limit():
    text = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read().replace("\r\n", "\n")
    assert re.findall(r"^run_optional_step (\d+) ", text, flags=re.M) == ["8"]
    assert 'run_optional_step 8 "quarterly fundamentals updater" 3300 mechanism/data_updaters/quarterly_fundamentals_updater.py --time-budget-seconds 2700' in text
    assert "run_step 9 " in text and "run_step 5 " in text                      # every other step still aborts the pipeline on failure
