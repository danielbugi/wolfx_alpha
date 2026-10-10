"""The collector's read-only VERIFICATION of the forward sector history, against a real Postgres schema with migration 31 and the REAL recorder as the
only writer (the collector's step never writes: it is shown below to leave the sector tables byte-identical).

Time: the recorder stamps with the database's real clock (that is the production behaviour), so the session under test is today's UTC date and the run's
clock is injected -- before the session's decision deadline for the "still repairable" cases, after it for the "permanent" ones."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import forward_world as FW
from data_updaters import sector_history_recorder as R
from forward_collection import cli as CLI
from forward_collection import contract as C
from forward_collection import orchestrator as O
from forward_collection import steps as S
from research.lab import dataset_reader as RD
from research.lab import sector_history_verification as V

ROOT = FW.ROOT
SRC = R.AUTHORITATIVE_SOURCE
GRACE = 1
UTC = timezone.utc
N = FW.SMALL_UNIVERSE


def _apply_migration_31(world):
    cur = world.admin.cursor()
    cur.execute(f'SET search_path TO "{world.schema}"')
    with open(os.path.join(ROOT, "mechanism", "add_sector_history_tables.sql"), encoding="utf-8") as fh:
        cur.execute(fh.read())
    world.admin.commit()


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=3, n_stocks=N)


@pytest.fixture
def sworld(world):
    _apply_migration_31(world)
    session = datetime.now(UTC).date()
    with world.connect() as conn:
        cur = conn.cursor()
        cur.executemany("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES (%s,%s,1,1,1,1,1)", [(s, session) for s in world.syms])
        conn.commit()
    world.session = session
    return world


def before_deadline(w):
    return lambda: datetime.now(UTC)


def after_deadline(w):
    t = O.decision_deadline(w.session, GRACE) + timedelta(hours=1)
    return lambda: t


def raw(sector, qt="EQUITY"):
    d = {"symbol": "X", "quoteType": qt, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
    if sector is not None:
        d["sector"] = sector
    return d


def poll(w, symbols, kind="sector", run_id="run-1"):
    """The REAL recorder, as the fundamentals updater drives it."""
    rec = R.SectorRecorder(run_id=run_id, connect=w.connect, is_enabled=lambda: True)
    for s in symbols:
        if kind == "sector":
            r = raw("Technology")
            rec.record(s, SRC, R.yfinance_company_info(r), meta=R.yfinance_meta(r))
        elif kind == "etf":
            r = raw(None, "ETF")
            rec.record(s, SRC, R.yfinance_company_info(r), meta=R.yfinance_meta(r))
        elif kind == "failed":
            rec.record(s, SRC, None, error=TimeoutError("vendor timed out"))
        else:
            raise AssertionError(kind)
    assert rec.counters["failed"] == 0
    return rec


def step(w, clock):
    ctx = O.Ctx(w.connect, w.session, False, None, GRACE, O.decision_deadline(w.session, GRACE), {}, clock)
    return S.make_sector_history_verify()(ctx)


def table_fingerprint(w):
    with w.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT (SELECT count(*) FROM sector_observation), (SELECT count(*) FROM sector_poll), "
                    "(SELECT md5(string_agg(value_hash, ',' ORDER BY id)) FROM sector_observation)")
        return cur.fetchone()


# ------------------------------------------------------------------------------------------------------------------------ the dependency, case by case
def test_without_migration_31_the_dependency_fails_visibly_and_is_permanent_only_after_the_deadline(world):
    world.session = datetime.now(UTC).date()
    r = step(world, before_deadline(world))
    assert r.outcome == C.FAILED and r.detail["reason"] == V.TABLES_ABSENT and r.detail["permanent_problems"] == []
    late = step(world, after_deadline(world))
    assert late.detail["permanent_problems"] == [V.TABLES_ABSENT] and late.detail["past_decision_deadline"] is True


def test_fundamentals_did_not_run_is_incomplete_before_the_deadline_and_permanent_after_it(sworld):
    r = step(sworld, before_deadline(sworld))
    assert r.outcome == C.FAILED and r.detail["reason"] == V.REFRESH_ABSENT and r.detail["permanent_problems"] == []
    assert r.detail["universe"] == N and r.detail["never_polled"] == N
    late = step(sworld, after_deadline(sworld))
    assert late.detail["permanent_problems"] == [V.REFRESH_ABSENT]


def test_a_full_refresh_by_the_real_recorder_satisfies_the_dependency(sworld):
    poll(sworld, sworld.syms)
    r = step(sworld, before_deadline(sworld))
    assert r.outcome == C.ALREADY and r.detail["satisfied"] is True and r.detail["accounted"] == N and r.detail["reasons"] == []
    assert r.detail["history_by_kind"] == {"observed": N}


def test_a_refresh_is_still_accepted_after_the_deadline_for_a_session_that_was_refreshed_in_time(sworld):
    poll(sworld, sworld.syms)
    assert step(sworld, after_deadline(sworld)).outcome == C.ALREADY


def test_option_b_legitimate_no_sector_symbols_are_accounted_and_never_fail_the_session(sworld):
    poll(sworld, sworld.syms[:60])
    poll(sworld, sworld.syms[60:], kind="etf", run_id="run-2")
    r = step(sworld, before_deadline(sworld))
    assert r.outcome == C.ALREADY and r.detail["accounted"] == N
    assert r.detail["history_by_kind"] == {"inferred_no_sector": N - 60, "observed": 60}


def test_a_vendor_that_failed_for_the_whole_universe_is_vendor_failure_not_a_missing_refresh(sworld):
    poll(sworld, sworld.syms, kind="failed")
    r = step(sworld, before_deadline(sworld))
    assert r.outcome == C.FAILED and r.detail["reason"] == V.VENDOR_FAILURE and r.detail["failed_only"] == N and r.detail["history_by_kind"] == {"no_history": N}


def test_a_partial_refresh_is_partial_refresh_and_the_ninety_percent_floor_is_inclusive(sworld):
    poll(sworld, sworld.syms[:N * 9 // 10 - 1])                                       # 71 of 80 (88.75 %): below the floor
    r = step(sworld, before_deadline(sworld))
    assert r.detail["reason"] == V.PARTIAL_REFRESH and r.detail["never_polled"] == N - (N * 9 // 10 - 1)
    poll(sworld, sworld.syms[N * 9 // 10 - 1:N * 9 // 10], run_id="run-2")            # the 72nd symbol: exactly 90 %
    assert step(sworld, before_deadline(sworld)).outcome == C.ALREADY


def test_a_failed_poll_followed_by_a_good_one_is_accounted(sworld):
    poll(sworld, sworld.syms, kind="failed")
    poll(sworld, sworld.syms, run_id="run-2")
    assert step(sworld, before_deadline(sworld)).outcome == C.ALREADY


def test_polls_older_than_the_cadence_window_do_not_count(sworld):
    old = datetime.now(UTC) - timedelta(days=30)
    with sworld.connect() as conn:                                       # test scaffolding in a disposable schema: a historical stamp, seeded once
        cur = conn.cursor()
        cur.execute("ALTER TABLE sector_poll DISABLE TRIGGER sector_poll_stamp")
        try:
            cur.executemany("INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, failure_reason, attempted_at, writer, code_ref) "
                            "VALUES ('old-run', %s, %s, 'invalid_response', 'none', 'response_too_sparse', %s, 'test_seed', 'test')",
                            [(s, SRC, old) for s in sworld.syms])
        finally:
            cur.execute("ALTER TABLE sector_poll ENABLE ALWAYS TRIGGER sector_poll_stamp")
        conn.commit()
    assert step(sworld, before_deadline(sworld)).detail["reason"] == V.REFRESH_ABSENT


def test_the_step_is_read_only_the_sector_tables_are_byte_identical_after_it_runs(sworld):
    poll(sworld, sworld.syms[:10])
    before = table_fingerprint(sworld)
    step(sworld, before_deadline(sworld))
    step(sworld, after_deadline(sworld))
    assert table_fingerprint(sworld) == before


def test_the_reader_additions_return_the_documented_columns_and_the_one_shared_history_query(sworld):
    poll(sworld, sworld.syms[:3])
    poll(sworld, sworld.syms[:1], kind="failed", run_id="run-2")
    with sworld.connect() as conn:
        cur = conn.cursor()
        assert RD.sector_history_tables_present(cur) is True
        lo, hi = V.window(sworld.session, GRACE)
        act = RD.poll_activity(cur, sworld.syms, SRC, lo, hi)
        assert {tuple(sorted(a)) for a in act} == {tuple(sorted(RD.POLL_ACTIVITY_COLUMNS))}
        assert sorted((a["symbol"], a["response_state"], a["polls"]) for a in act) == sorted(
            [(sworld.syms[0], "request_failed", 1), (sworld.syms[0], "sector", 1), (sworld.syms[1], "sector", 1), (sworld.syms[2], "sector", 1)])
        rows = RD.history_rows(cur, sworld.syms, SRC, sworld.session, hi)
        assert len([r for r in rows if r["row_kind"] == "observation"]) == 3
        assert RD.poll_activity(cur, sworld.syms, "tiingo_meta", lo, hi) == []         # a diagnostic source never counts toward the authoritative one
        conn.rollback()


# ------------------------------------------------------------------------------------------------------------------------ inside the orchestrator
def stub_steps(sector_step):
    ok = lambda n: (lambda ctx: C.StepResult(n, C.OK, {}))  # noqa: E731
    return {C.STEP_OBSERVE: ok(C.STEP_OBSERVE), C.STEP_LABELS: ok(C.STEP_LABELS), C.STEP_VERIFY: ok(C.STEP_VERIFY), C.STEP_SECTOR_HISTORY: sector_step}


def run(w, clock):
    return O.run_session(w.connect, w.session, stub_steps(S.make_sector_history_verify()), apply=True, grace_days=GRACE, code_ref="t", clock=clock)


def test_a_session_with_a_refreshed_history_is_complete(sworld):
    poll(sworld, sworld.syms)
    rep = run(sworld, before_deadline(sworld))
    assert rep.verdict == C.COMPLETE and rep.exit_code() == C.EXIT_COMPLETE


def test_a_missing_refresh_is_incomplete_before_the_deadline_never_hidden_by_a_successful_market_write(sworld):
    rep = run(sworld, before_deadline(sworld))
    outcomes = {s.name: s.outcome for s in rep.steps}
    assert rep.verdict == C.INCOMPLETE and rep.exit_code() == C.EXIT_INCOMPLETE
    assert outcomes[C.STEP_OBSERVE] == C.OK and outcomes[C.STEP_VERIFY] == C.OK and outcomes[C.STEP_SECTOR_HISTORY] == C.FAILED


def test_a_missing_refresh_after_the_deadline_is_missed_with_the_sector_specific_explanation(sworld):
    rep = run(sworld, after_deadline(sworld))
    assert rep.verdict == C.MISSED and rep.exit_code() == C.EXIT_MISSED
    assert "sector history" in rep.next_action.lower() or any("sector history" in m.lower() for m in ([rep.next_action] + list(rep.missing)))


def test_the_dependency_is_optional_by_being_absent_from_the_step_set(sworld):
    steps = stub_steps(None)
    del steps[C.STEP_SECTOR_HISTORY]
    rep = O.run_session(sworld.connect, sworld.session, steps, apply=True, grace_days=GRACE, code_ref="t", clock=before_deadline(sworld))
    assert rep.verdict == C.COMPLETE and C.STEP_SECTOR_HISTORY not in {s.name for s in rep.steps}


# ------------------------------------------------------------------------------------------------------------------------ the CLI surface
def run_cli(w, clock):
    cal = {w.session: FW.sessions_calendar(w)[w.sessions[0]]}
    out = []
    rc = CLI.main(["verify-sector-history", "--session", w.session.isoformat()], connect=w.connect, clock=clock,
                  sessions_provider=lambda now: (cal, "calendar"), out=out.append)
    return rc, json.loads(out[-1])


def test_the_read_only_verify_sector_history_command_reports_the_same_judgement_and_exit_codes(sworld):
    rc, doc = run_cli(sworld, before_deadline(sworld))
    assert rc == C.EXIT_INCOMPLETE and doc["detail"]["reason"] == V.REFRESH_ABSENT and doc["outcome"] == C.FAILED
    assert run_cli(sworld, after_deadline(sworld))[0] == C.EXIT_MISSED
    poll(sworld, sworld.syms)
    rc, doc = run_cli(sworld, before_deadline(sworld))
    assert rc == C.EXIT_COMPLETE and doc["detail"]["satisfied"] is True


def test_the_run_command_adds_the_sector_dependency_only_behind_its_flag(sworld, monkeypatch):
    seen = []

    def fake_make_steps(**kw):
        seen.append(kw)
        steps = stub_steps(S.make_sector_history_verify())
        if not kw.get("with_sector_history"):
            del steps[C.STEP_SECTOR_HISTORY]
        return steps

    monkeypatch.setattr(S, "make_steps", fake_make_steps)
    cal = {sworld.session: FW.sessions_calendar(sworld)[sworld.sessions[0]]}

    def cli_run(*extra):
        out = []
        rc = CLI.main(["run", "--session", sworld.session.isoformat(), "--apply", "--code-ref", "t", *extra], connect=sworld.connect,
                      clock=before_deadline(sworld), sessions_provider=lambda now: (cal, "calendar"), out=out.append)
        return rc, json.loads(out[-1])

    rc, doc = cli_run()
    assert rc == C.EXIT_COMPLETE and seen[-1]["with_sector_history"] is False
    rc, doc = cli_run("--with-sector-history-check")
    assert rc == C.EXIT_INCOMPLETE and seen[-1]["with_sector_history"] is True           # no refresh happened: visibly incomplete, not hidden
    poll(sworld, sworld.syms)
    assert cli_run("--with-sector-history-check")[0] == C.EXIT_COMPLETE
