"""The orchestrator + the real writers in a disposable world: idempotency, partial failure, atomicity, retry, missed sessions, catch-up, locking,
post-deadline refusal, reconstructed rows, restated bars. Every row is written by the real capture hook, the real Market Intelligence writer and the real
label writer; a failure is injected AROUND them (a wrapped connection or a step wrapper), never by inserting an "ideal" row."""
from datetime import timedelta

import psycopg2
import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import orchestrator as O
from forward_collection import steps as S

K = 10          # candidates per simulated session
TABLES = ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength", "forward_return_label")


@pytest.fixture
def world(monkeypatch):
    FW.scale_universe_minimums(monkeypatch)
    yield from FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE)


def start(w):
    return FW.start_world(w)


def evening(w, k, strat):
    FW.evening(w, k, strat, K)


def morning(w, k, steps, *, at=None, **kw):
    w.now = at or (C.fire_instants_utc(w.sessions[k])[0] + timedelta(minutes=1))
    return O.run_session(w.connect, w.sessions[k], steps, apply=kw.pop("apply", True), grace_days=1, code_ref="t", clock=w.clock, **kw)


def counts(w):
    return {t: w.count(t) for t in TABLES}


def outcomes(rep):
    return {s.name: s.outcome for s in rep.steps}


# ---------------------------------------------------------------------------------------------------------- the happy path and idempotency
def test_a_session_is_complete_and_a_rerun_writes_nothing(world):
    strat, steps = start(world)
    for k in range(3):
        evening(world, k, strat)
        rep = morning(world, k, steps)
        assert rep.verdict == C.COMPLETE, rep.to_dict()
    before = counts(world)
    assert before["market_snapshot"] == 3 and before["sector_snapshot"] > 0
    assert before["stock_relative_strength"] == 3 * len(C.COLLECTOR_VERSIONS["stock_rs"]["horizons"]) * FW.SMALL_UNIVERSE
    assert before["forward_return_label"] > 0
    again = morning(world, 2, steps)
    assert again.verdict == C.COMPLETE
    assert outcomes(again) == {C.STEP_CAPTURE: C.ALREADY, C.STEP_OBSERVE: C.ALREADY, C.STEP_LABELS: C.ALREADY, C.STEP_VERIFY: C.OK}
    assert counts(world) == before


def test_a_dry_run_writes_nothing_and_never_says_complete(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    rep = morning(world, 0, steps, apply=False)
    assert rep.verdict == C.DRY_RUN
    assert all(v == 0 for v in counts(world).values())


def test_every_collected_row_is_observed_and_stamped_before_its_deadline(world):
    strat, steps = start(world)
    for k in range(2):
        evening(world, k, strat)
        assert morning(world, k, steps).verdict == C.COMPLETE
    with world.connect() as conn:
        cur = conn.cursor()
        for table, col in (("market_snapshot", "captured_at"), ("sector_snapshot", "captured_at"), ("stock_relative_strength", "created_at"),
                           ("universe_snapshot", "captured_at")):
            cur.execute(f"SELECT DISTINCT provenance FROM {table}")
            assert {r[0] for r in cur.fetchall()} == {"observed"}, table
            cur.execute(f"SELECT count(*) FROM {table} WHERE {col} >= (session_date + 2)::timestamp AT TIME ZONE 'UTC'")
            assert cur.fetchone()[0] == 0, table          # grace 1 => decision deadline = 00:00 UTC of session + 2
        conn.rollback()


# ---------------------------------------------------------------------------------------------------------- partial failure, atomicity, retry
def test_one_failed_step_does_not_stop_the_others_and_the_rerun_repairs_it(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    real_observe = steps[C.STEP_OBSERVE]
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the observation writer broke")
        return real_observe(ctx)

    first = morning(world, 0, dict(steps, **{C.STEP_OBSERVE: flaky}))
    assert first.verdict == C.INCOMPLETE and first.missing == [C.STEP_OBSERVE, C.STEP_VERIFY]
    assert outcomes(first)[C.STEP_OBSERVE] == C.FAILED and outcomes(first)[C.STEP_VERIFY] == C.SKIPPED      # verify never fabricates success
    assert outcomes(first)[C.STEP_LABELS] in C.SUCCESS                                                       # labels did not wait for observe
    assert counts(world)["market_snapshot"] == 0
    second = morning(world, 0, dict(steps, **{C.STEP_OBSERVE: flaky}), at=C.fire_instants_utc(world.sessions[0])[1])
    assert second.verdict == C.COMPLETE, second.to_dict()


class _FailOnRsInsert(psycopg2.extensions.cursor):
    armed = True

    def execute(self, query, vars=None):
        text = query.decode() if isinstance(query, bytes) else str(query)
        if _FailOnRsInsert.armed and "INSERT INTO stock_relative_strength" in text:
            raise psycopg2.OperationalError("connection to the server was lost (injected)")
        return super().execute(query, vars)


def test_the_observation_is_atomic_a_crash_midway_leaves_no_partial_session(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    real_connect = world.connect

    from contextlib import contextmanager

    @contextmanager
    def crashing():
        with real_connect() as conn:
            conn.cursor_factory = _FailOnRsInsert
            yield conn

    _FailOnRsInsert.armed = True
    try:
        rep = O.run_session(crashing, world.sessions[0], {C.STEP_OBSERVE: S.make_observe(), C.STEP_VERIFY: S.make_verify()}, apply=True, grace_days=1,
                            code_ref="t", clock=lambda: C.fire_instants_utc(world.sessions[0])[0], max_attempts=2, sleep=lambda s: None)
    finally:
        _FailOnRsInsert.armed = False
    obs = {s.name: s for s in rep.steps}[C.STEP_OBSERVE]
    assert obs.outcome == C.FAILED and obs.detail.get("transient") and obs.attempts == 2
    assert rep.verdict == C.INCOMPLETE
    assert counts(world) == {t: 0 for t in TABLES}                    # nothing partial: not the universe, market or sector rows either
    world.now = C.fire_instants_utc(world.sessions[0])[1]
    rep2 = O.run_session(world.connect, world.sessions[0], steps, apply=True, grace_days=1, code_ref="t", clock=world.clock)
    assert rep2.verdict == C.COMPLETE


def test_a_session_missing_only_its_relative_strength_rows_is_repaired_by_the_rerun(world):
    from market_intelligence import runner as mi
    strat, steps = start(world)
    evening(world, 0, strat)
    world.now = C.fire_instants_utc(world.sessions[0])[0]
    mi.run(world.connect, world.sessions[0], provenance="observed", apply=True, code_ref="t", with_stock_rs=False)     # a crash after the market write
    assert world.count("market_snapshot") == 1 and world.count("stock_relative_strength") == 0
    rep = morning(world, 0, steps, at=C.fire_instants_utc(world.sessions[0])[0] + timedelta(minutes=5))
    assert rep.verdict == C.COMPLETE and outcomes(rep)[C.STEP_OBSERVE] == C.OK
    assert world.count("market_snapshot") == 1 and world.count("stock_relative_strength") == 3 * FW.SMALL_UNIVERSE


# ---------------------------------------------------------------------------------------------------------- locking, deadlines, missed sessions
def test_a_duplicate_invocation_is_refused_by_the_database_lock_and_writes_nothing(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    with world.connect() as holder:
        cur = holder.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (O.LOCK_KEY,))
        assert cur.fetchone()[0]
        holder.commit()
        rep = morning(world, 0, steps)
        assert rep.verdict == C.LOCKED and rep.steps == [] and rep.exit_code() == C.EXIT_LOCKED
        assert all(v == 0 for v in counts(world).values())
        cur.execute("SELECT pg_advisory_unlock(%s)", (O.LOCK_KEY,))
        holder.commit()
    assert morning(world, 0, steps).verdict == C.COMPLETE            # released: the next fire proceeds


def test_after_the_decision_deadline_the_session_is_refused_never_stamped_late(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    rep = morning(world, 0, steps, at=C.fire_instants_utc(world.sessions[0])[0] + timedelta(days=1))      # past 00:00 UTC of session + 2
    assert rep.verdict == C.MISSED and rep.exit_code() == C.EXIT_MISSED
    assert outcomes(rep)[C.STEP_OBSERVE] == C.REFUSED
    c = counts(world)
    assert c["market_snapshot"] == c["sector_snapshot"] == c["stock_relative_strength"] == c["universe_snapshot"] == 0


def test_a_missed_session_stays_missed_a_later_session_is_unaffected_and_nothing_is_backfilled(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    assert morning(world, 0, steps).verdict == C.COMPLETE
    evening(world, 1, strat)                       # session 1: the collector does not run that morning
    evening(world, 2, strat)                       # session 2's bars are now the newest
    late = morning(world, 1, steps, at=C.fire_instants_utc(world.sessions[1])[1])
    assert late.verdict == C.MISSED and outcomes(late)[C.STEP_OBSERVE] == C.FAILED
    assert late.steps[1].detail["reason"] == C.REASON_NOT_LATEST
    assert world.count("market_snapshot", "session_date = %s", (world.sessions[1],)) == 0
    assert morning(world, 2, steps).verdict == C.COMPLETE
    assert world.count("market_snapshot") == 2
    rep_again = morning(world, 1, steps, at=C.fire_instants_utc(world.sessions[2])[1])
    assert rep_again.verdict == C.MISSED                                  # still missed on every later look; the gap is never filled


def test_an_on_time_session_re_run_after_the_next_bars_arrive_is_still_complete(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    assert morning(world, 0, steps).verdict == C.COMPLETE
    evening(world, 1, strat)
    rep = morning(world, 0, steps, at=C.fire_instants_utc(world.sessions[0])[1])
    assert rep.verdict == C.COMPLETE and outcomes(rep)[C.STEP_OBSERVE] == C.ALREADY


def test_an_unloaded_session_is_incomplete_not_missed_and_a_later_fire_can_still_collect_it(world):
    strat, steps = start(world)
    world.now = world.at(world.sessions[0], 21, 30)
    rep = O.run_session(world.connect, world.sessions[0], {C.STEP_OBSERVE: S.make_observe(), C.STEP_VERIFY: S.make_verify()}, apply=True, grace_days=1,
                        code_ref="t", clock=lambda: C.fire_instants_utc(world.sessions[0])[0])
    assert rep.verdict == C.INCOMPLETE and rep.steps[0].detail["reason"] == C.REASON_NOT_LOADED
    evening(world, 0, strat)
    assert morning(world, 0, steps).verdict == C.COMPLETE


def test_a_session_without_a_candidate_capture_is_incomplete_when_capture_is_required(world):
    strat, steps = start(world)
    world.load_session(0)
    rep = morning(world, 0, steps)
    assert rep.verdict == C.INCOMPLETE and C.STEP_CAPTURE in rep.missing and outcomes(rep)[C.STEP_OBSERVE] in C.SUCCESS
    assert "cannot" in rep.steps[0].error
    no_cap = morning(world, 0, S.make_steps(feature_set_version="mi_v2", with_capture=False))
    assert no_cap.verdict == C.COMPLETE                                  # capture checking is opt-in; the market/sector/RS/label stack is still complete


# ---------------------------------------------------------------------------------------------------------- labels: maturity, catch-up, no rewrite
def _labels(w):
    with w.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT symbol, t0_session, horizon_sessions, horizon_session, label_status, raw_return, input_hash, computed_as_of_session "
                    "FROM forward_return_label ORDER BY symbol, t0_session, horizon_sessions")
        rows = cur.fetchall()
        conn.rollback()
    return rows


def test_a_label_exists_only_from_the_session_its_horizon_has_matured(world):
    strat, steps = start(world)
    horizons = C.COLLECTOR_VERSIONS["label_horizons"]
    seen = {}
    for k in range(12):
        evening(world, k, strat)
        assert morning(world, k, steps).verdict == C.COMPLETE
        got = {}
        for sym, t0, h, hs, status, raw, ih, asof in _labels(world):
            if t0 == world.sessions[0]:
                got[h] = (hs, asof)
        seen[k] = got
    for h in horizons:
        if h > 11:
            assert all(h not in seen[k] for k in seen)
            continue
        first = min(k for k in seen if h in seen[k])
        assert first == h, (h, first)                       # exactly when session t0 + h completes, never earlier
        hs, asof = seen[first][h]
        assert hs == world.sessions[h] and asof >= hs


def test_labels_catch_up_after_missed_runs_and_equal_the_continuous_world(world):
    strat, steps = start(world)
    for k in range(9):
        evening(world, k, strat)
        if k < 3 or k == 8:                                   # the collector did not run on sessions 3..7 (the screener's capture still did)
            assert morning(world, k, steps).verdict == C.COMPLETE
    assert world.count("market_snapshot") == 4               # only the runs that happened were observed; nothing was invented for the gap
    caught_up = _labels(world)
    assert caught_up

    gen = FW.make_world(n_days=16, n_stocks=FW.SMALL_UNIVERSE)       # the same market, every session collected on time
    w2 = next(gen)
    try:
        strat2, steps2 = start(w2)
        for k in range(9):
            evening(w2, k, strat2)
            assert morning(w2, k, steps2).verdict == C.COMPLETE
        want = _labels(w2)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
    assert [r[:-1] for r in caught_up] == [r[:-1] for r in want]      # same labels, same values, same input hashes: catching up changes nothing
    assert all(a[-1] >= b[-1] for a, b in zip(caught_up, want))        # ...and the record honestly says WHEN each was computed (late, never backdated)


def test_a_label_is_never_rewritten_when_a_bar_is_restated_it_is_only_reported(world):
    strat, steps = start(world)
    for k in range(4):
        evening(world, k, strat)
        assert morning(world, k, steps).verdict == C.COMPLETE
    before = _labels(world)
    assert before
    with world.connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE stock_prices SET close = close * 1.5, high = high * 1.5 WHERE date = %s", (world.sessions[2],))   # a vendor restatement
        conn.commit()
    rep = morning(world, 3, steps, at=C.fire_instants_utc(world.sessions[3])[1])
    assert outcomes(rep)[C.STEP_LABELS] == C.ALREADY
    assert _labels(world) == before                                       # an immutable label is never rewritten...
    from research.labels import runner as label_runner
    report = label_runner.restatement_report(world.connect, world.sessions[3])
    assert report and {r["change"] for r in report} <= {"inputs_restated", "inputs_no_longer_available"}   # ...the existing read-only restatement report is what surfaces it


# ---------------------------------------------------------------------------------------------------------- provenance and the database's own defences
def test_a_reconstructed_row_never_counts_as_an_observation(world):
    from market_intelligence import runner as mi
    strat, steps = start(world)
    evening(world, 0, strat)
    world.now = C.fire_instants_utc(world.sessions[0])[0]
    mi.run(world.connect, world.sessions[0], provenance="reconstructed", apply=True, code_ref="t", with_stock_rs=True)
    assert world.count("market_snapshot", "provenance = 'reconstructed'") == 1
    ctx = O.Ctx(world.connect, world.sessions[0], True, "t", 1, O.decision_deadline(world.sessions[0], 1), {})
    v = S.make_verify()(ctx)
    assert v.outcome == C.FAILED and "no observed market_snapshot" in v.error and v.detail["reconstructed_rows_ignored"] > 0
    rep = morning(world, 0, steps)
    assert rep.verdict == C.COMPLETE
    assert world.count("market_snapshot", "provenance = 'observed'") == 1 and world.count("market_snapshot", "provenance = 'reconstructed'") == 1


def test_an_unknown_provenance_is_rejected_by_the_database(world):
    strat, steps = start(world)
    evening(world, 0, strat)
    assert morning(world, 0, steps).verdict == C.COMPLETE
    with world.connect() as conn:
        cur = conn.cursor()
        for table in ("universe_snapshot", "market_snapshot", "sector_snapshot", "stock_relative_strength"):
            cur.execute("SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c WHERE c.conrelid = %s::regclass AND c.contype = 'c' "
                        "AND pg_get_constraintdef(c.oid) LIKE %s", (table, "%provenance%observed%reconstructed%"))
            assert cur.fetchall(), f"{table} has no provenance CHECK limited to observed/reconstructed"
        with pytest.raises(psycopg2.errors.IntegrityConstraintViolation):
            cur.execute("UPDATE market_snapshot SET provenance = 'unknown'")      # and the rows are append-only anyway
        conn.rollback()


def test_a_session_with_symbols_lacking_a_sector_is_collected_completely_and_reports_the_unsafe_cells():
    gen = FW.make_world(n_days=8, n_stocks=FW.SMALL_UNIVERSE, no_sector=("S0003", "S0004"))
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    FW.scale_universe_minimums(mp)
    w = next(gen)
    try:
        strat, steps = start(w)
        evening(w, 0, strat)
        rep = morning(w, 0, steps)
        assert rep.verdict == C.COMPLETE                     # collection is complete; what the missing sector MEANS is the owner's policy decision
        v = [s for s in rep.steps if s.name == C.STEP_VERIFY][0]
        assert v.detail["rs_ok_cells_without_sector"] == 2 * 3 and v.detail["rs_ok_cells_not_sector_pit_safe"] >= 2 * 3
        assert w.count("stock_relative_strength", "state = 'ok' AND sector IS NULL AND session_date = %s", (w.sessions[0],)) == 2 * 3
    finally:
        mp.undo()
        try:
            next(gen)
        except StopIteration:
            pass
