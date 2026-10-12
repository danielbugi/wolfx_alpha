"""The CLI: dry-run by default, --apply needs --code-ref, fail-closed on a non-session / weekday-fallback calendar, verify exit codes."""
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

import forward_world as FW
from forward_collection import cli
from forward_collection import contract as C

K = 25


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=12, n_stocks=FW.SMALL_UNIVERSE)


def run_cli(w, argv, *, source="calendar", at=None, sessions=None):
    w.now = at or (C.fire_instants_utc(w.sessions[3])[0] + timedelta(minutes=1))
    out = []
    cal = sessions if sessions is not None else FW.sessions_calendar(w)
    rc = cli.main(argv, connect=w.connect, clock=w.clock, sessions_provider=lambda now: (cal, source), out=out.append)
    return rc, (json.loads(out[-1]) if out else None)


def loaded(w, upto=3):
    strat, _ = FW.start_world(w)
    for k in range(upto + 1):
        FW.evening(w, k, strat, K)
    return strat


def test_default_is_a_dry_run_that_writes_nothing(world):
    loaded(world)
    before = {t: world.count(t) for t in ("universe_snapshot", "market_snapshot", "stock_relative_strength", "forward_return_label")}
    rc, doc = run_cli(world, ["run", "--latest-completed"])
    assert rc == C.EXIT_COMPLETE and doc["verdict"] == C.DRY_RUN
    assert {t: world.count(t) for t in before} == before


def test_apply_without_a_code_ref_is_refused_before_any_connection(world):
    def boom():
        raise AssertionError("must not connect")
    assert cli.main(["run", "--latest-completed", "--apply"], connect=boom) == C.EXIT_REFUSED


def test_apply_collects_then_a_second_apply_is_idempotent_and_verify_agrees(world):
    loaded(world)
    rc, doc = run_cli(world, ["run", "--latest-completed", "--apply", "--code-ref", "sim"])
    assert rc == C.EXIT_COMPLETE and doc["verdict"] == C.COMPLETE and doc["session_resolution"] == "latest completed session"
    n = world.count("stock_relative_strength")
    assert n > 0
    rc2, doc2 = run_cli(world, ["run", "--latest-completed", "--apply", "--code-ref", "sim"])
    assert rc2 == C.EXIT_COMPLETE and doc2["verdict"] == C.COMPLETE and world.count("stock_relative_strength") == n
    rc3, doc3 = run_cli(world, ["verify", "--latest-completed"])
    assert rc3 == C.EXIT_COMPLETE and doc3["outcome"] in (C.OK, C.ALREADY)


def test_verify_of_a_session_that_was_never_collected_is_incomplete_not_a_success(world):
    loaded(world)
    rc, doc = run_cli(world, ["verify", "--latest-completed"])
    assert rc in (C.EXIT_INCOMPLETE, C.EXIT_MISSED) and doc["outcome"] != C.OK


def test_an_off_calendar_explicit_session_is_refused(world, capsys):
    loaded(world)
    saturday = world.sessions[3] + timedelta(days=(5 - world.sessions[3].weekday()) % 7 or 7)
    rc, doc = run_cli(world, ["run", "--session", saturday.isoformat(), "--apply", "--code-ref", "sim"])
    assert rc == C.EXIT_REFUSED and doc is None
    assert "not a US trading session" in capsys.readouterr().err
    assert world.count("market_snapshot") == 0


def test_a_weekday_fallback_calendar_fails_closed(world, capsys):
    loaded(world)
    rc, _ = run_cli(world, ["run", "--latest-completed", "--apply", "--code-ref", "sim"], source="weekday-fallback")
    assert rc == C.EXIT_REFUSED and "weekday fallback" in capsys.readouterr().err
    assert world.count("market_snapshot") == 0


def test_a_future_session_is_refused(world):
    loaded(world)
    rc, _ = run_cli(world, ["run", "--session", world.sessions[8].isoformat(), "--apply", "--code-ref", "sim"])
    assert rc == C.EXIT_REFUSED


def test_module_entrypoint_imports_and_prints_help():
    root = Path(FW.ROOT)
    r = subprocess.run([sys.executable, "-m", "forward_collection", "--help"], cwd=root / "mechanism", capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "run" in r.stdout and "preflight" in r.stdout
