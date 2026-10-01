"""Run end states (complete / partial / failed), the activation gate, ATR provenance and the 250-symbol chunking of
the observer -- including the golden-master proof that chunked capture equals unchunked capture."""
from contextlib import contextmanager
from datetime import timedelta

import pytest

from research import observer, repository
from screeners.signal_ledger_writer import StrategyRef
from test_observer import World, assert_identity, final_of, run, table, three_symbol_world  # noqa: F401
from test_observer import world  # noqa: F401  (fixture)
from test_snapshot_builder import make_rows

BASE = dict(candidates=3, captured=3, already_captured=0, stale_skipped=0, snapshot_skipped=0, invalid_skipped=0,
            guard_not_evaluated=0)


# ----------------------------------------------------------------------------------------------- end states
@pytest.mark.parametrize("over,error,expected", [
    ({}, None, "complete"),
    (dict(candidates=5, stale_skipped=2), None, "complete"),                      # stale is expected + accounted
    (dict(captured=0, already_captured=3), None, "complete"),                     # an idempotent re-run
    (dict(candidates=0, captured=0), None, "complete"),                           # nothing to capture is not a failure
    (dict(captured=2, snapshot_skipped=1), None, "partial"),
    (dict(captured=2, invalid_skipped=1), None, "partial"),
    (dict(guard_not_evaluated=3), None, "partial"),
    (dict(captured=2), None, "partial"),                                          # counters do not reconcile
    (dict(candidates=4), None, "partial"),
    ({}, "boom", "failed"),
    (dict(captured=2, invalid_skipped=1), "boom", "failed"),
])
def test_final_status_is_deterministic(over, error, expected):
    counters = dict(BASE, **over)
    assert observer.final_status(counters, error) == expected
    assert observer.final_status(dict(counters), error) == expected


def test_a_clean_session_is_complete(world):
    a, b, c = three_symbol_world(world)
    res = run(world, [a, b, c], decisions={"AAA": [], "BBB": [], "CCC": []})
    assert res.status == "complete"
    assert table(world, "SELECT status, run_finished_at IS NOT NULL, error FROM candidate_capture_run") == [
        ("complete", True, None)]


def test_a_skipped_snapshot_makes_the_run_partial(world):
    world.add_prices("AAA", make_rows(seed=3))
    a = world.candidate("AAA")
    tiny = dict(a, symbol="TINY")                        # a candidate with no price history at all
    res = run(world, [a, tiny], decisions={"AAA": [], "TINY": []}, session=world.rows["AAA"][-1]["date"])
    assert res.status == "partial" and res.counters["snapshot_skipped"] == 1 and res.counters["captured"] == 1
    assert_identity(res)
    assert table(world, "SELECT status, error FROM candidate_capture_run") == [("partial", None)]


def test_unavailable_guards_make_the_run_partial(world):
    world.add_prices("AAA", make_rows(seed=3))
    res = run(world, [world.candidate("AAA")], evaluated=False)
    assert res.status == "partial" and res.counters["captured"] == 1


def test_an_invalid_candidate_makes_the_run_partial(world):
    world.add_prices("AAA", make_rows(seed=3))
    res = run(world, [world.candidate("AAA", signal_type="sideways")])
    assert res.status == "partial" and res.counters["invalid_skipped"] == 1


def test_a_run_level_error_is_failed_not_partial(world, monkeypatch):
    world.add_prices("AAA", make_rows(seed=3))

    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(repository, "fetch_price_rows", boom)
    res = run(world, [world.candidate("AAA")])
    assert res.status == "failed" and "db hiccup" in res.error
    assert table(world, "SELECT status, error IS NOT NULL FROM candidate_capture_run") == [("failed", True)]


# ----------------------------------------------------------------------------------------- activation gate
def test_without_an_activation_boundary_nothing_is_captured(schema_env, seed):
    _, connect = schema_env
    w = World(connect, seed.strategy_id())
    seed.conn.commit()
    w.create_tables()                                    # deliberately no activate()
    w.add_prices("AAA", make_rows(seed=3))
    res = run(w, [w.candidate("AAA")], decisions={"AAA": []})
    assert res.status == "disabled" and res.reason == "not_active" and res.run_id is None
    for t in ("candidate_capture_run", "candidate_observation", "feature_snapshot", "feature_set_registry"):
        assert table(w, f"SELECT count(*) FROM {t}") == [(0,)], t


def test_the_boundary_decides_per_session(schema_env, seed):
    _, connect = schema_env
    w = World(connect, seed.strategy_id())
    seed.conn.commit()
    w.create_tables()
    rows = make_rows(seed=3, n=330)
    w.add_prices("AAA", rows)
    early, late = rows[300]["date"], rows[320]["date"]
    w.activate(effective_from=late)

    def cand(i):
        c = w.candidate("AAA", screening_date=rows[i]["date"])
        c["current_price"] = float(rows[i]["close"])
        return c

    assert run(w, [cand(300)], session=early).status == "disabled"
    assert run(w, [cand(320)], decisions={"AAA": []}, session=late).status == "complete"
    off = late + timedelta(days=5)
    w.activate(state="disabled", effective_from=off)
    res = run(w, [cand(320)], session=off + timedelta(days=1))
    assert res.status == "disabled" and res.reason == "disabled"
    assert table(w, "SELECT count(*) FROM candidate_capture_run") == [(1,)]


def test_activation_failure_never_raises(world, monkeypatch):
    world.add_prices("AAA", make_rows(seed=3))

    def boom(*a, **k):
        raise RuntimeError("no activation table")

    monkeypatch.setattr(repository, "activation_state", boom)
    res = run(world, [world.candidate("AAA")])
    assert res.status == "failed" and "no activation table" in res.error


# --------------------------------------------------------------------------------------- ATR provenance
@pytest.mark.parametrize("kw,expected", [
    (dict(atr_14=2.0), "measured"),
    (dict(atr_14=2.0, screener_defaults=["atr_14"]), "fallback"),
    (dict(atr_14=0.0), "missing"),
    (dict(atr_14=None), "missing"),
    (dict(atr_14=-1.0), "missing"),
    (dict(atr_14=0.0001, screener_defaults=["atr_14", "volume"]), "fallback"),
])
def test_atr_source_rule(kw, expected):
    assert observer.atr_source({"symbol": "X", **kw}) == expected


def test_atr_source_is_stored_on_the_observation(world):
    for i, s in enumerate(("AAA", "BBB", "CCC")):
        world.add_prices(s, make_rows(seed=3 + i))
    cands = [world.candidate("AAA"), world.candidate("BBB", atr_14=None),
             world.candidate("CCC", screener_defaults=["atr_14"])]
    run(world, cands, decisions={"AAA": [], "BBB": [], "CCC": []})
    assert dict(table(world, "SELECT symbol, atr_source FROM candidate_observation")) == {
        "AAA": "measured", "BBB": "missing", "CCC": "fallback"}


# ------------------------------------------------------------------------------------------- chunking
def many_symbol_world(world, n):
    cands, finals, decisions = [], [], {}
    for i in range(n):
        sym = f"S{i:03d}"
        world.add_prices(sym, make_rows(seed=100 + i))
        c = world.candidate(sym, "bullish_breakout" if i % 3 else "near_bearish")
        cands.append(c)
        decisions[sym] = [] if i % 5 else ["illiquid_dollar_volume"]
        if i % 5:
            finals.append(final_of(c))
    return cands, finals, decisions


def _capture(world, cands, finals, decisions, chunk_size, strategy=None):
    return observer.capture_session(candidates=cands, final_signals=finals, guard_decisions=decisions,
                                    guards_evaluated=True, session_date=world.rows["S000"][-1]["date"],
                                    strategy=strategy or world.strategy, connect=world.connect, chunk_size=chunk_size)


def test_chunked_capture_equals_unchunked_capture(world):
    """Golden master: one chunk vs many chunks give identical rows, decisions, ranks, hashes, links and counters.
    (The second run uses its own strategy id so it is a fresh identity space over the same snapshots.)"""
    cands, finals, decisions = many_symbol_world(world, 30)
    one = _capture(world, cands, finals, decisions, chunk_size=1000)
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO strategies (strategy_key, strategy_version) VALUES ('golden_b', 'v1') RETURNING id")
        sid = cur.fetchone()[0]
        c.commit()
    world.activate(strategy_id=sid)
    many = _capture(world, cands, finals, decisions, chunk_size=7, strategy=StrategyRef(sid, "golden_b", "v1"))

    assert one.status == many.status == "complete"
    assert (one.profile["chunks"], many.profile["chunks"]) == (1, 5) and many.profile["commits"] == 5
    assert one.counters == many.counters and one.counters["captured"] == 30
    rows = table(world, "SELECT o.strategy_id, o.symbol, o.direction, o.passed_guard, o.guard_reasons, o.session_rank, "
                        "o.atr_source, o.tracked_intent, o.ml_status, o.breakout_dist_atr, s.content_hash "
                        "FROM candidate_observation o JOIN feature_snapshot s ON s.id = o.snapshot_id "
                        "ORDER BY o.symbol, o.direction, o.strategy_id")
    first = [r[1:] for r in rows if r[0] != sid]
    second = [r[1:] for r in rows if r[0] == sid]
    assert first == second and len(first) == 30
    assert [k for k in one.links] == [k for k in many.links] == [(c["symbol"], 1 if c["signal_type"] != "near_bearish" else -1)
                                                                  for c in cands]
    assert {k: (v.feature_snapshot_id, v.feature_set_version) for k, v in one.links.items()} == \
           {k: (v.feature_snapshot_id, v.feature_set_version) for k, v in many.links.items()}
    assert table(world, "SELECT count(*) FROM feature_snapshot") == [(30,)]      # shared snapshots, written once


def test_chunking_preserves_order_and_first_write_wins(world):
    cands, finals, decisions = many_symbol_world(world, 12)
    first = _capture(world, cands, finals, decisions, chunk_size=5)
    assert [k[0] for k in first.links] == [c["symbol"] for c in cands]
    again = _capture(world, cands, finals, decisions, chunk_size=5)
    assert again.counters["captured"] == 0 and again.counters["already_captured"] == 12 and again.links == first.links


def test_a_bad_candidate_in_a_chunk_does_not_lose_its_neighbours(world, monkeypatch):
    cands, finals, decisions = many_symbol_world(world, 9)
    real = repository.insert_observation

    def flaky(cur, row):
        if row["symbol"] in ("S002", "S007"):
            raise RuntimeError("boom")
        return real(cur, row)

    monkeypatch.setattr(repository, "insert_observation", flaky)
    res = _capture(world, cands, finals, decisions, chunk_size=4)
    assert res.status == "partial" and res.counters["captured"] == 7 and res.counters["invalid_skipped"] == 2
    assert set(res.skipped) == {"S002", "S007"} and "boom" in res.skipped["S002"]
    assert table(world, "SELECT count(*) FROM feature_snapshot WHERE symbol IN ('S002', 'S007')") == [(0,)]
    assert_identity(res)




def test_a_failing_chunk_commit_is_retried_per_candidate(world):
    """If a chunk's single commit fails, nothing from it is counted and each candidate is redone and committed
    alone, so the final accounting equals what the unchunked path produces."""
    cands, finals, decisions = many_symbol_world(world, 6)

    @contextmanager
    def failing_first_chunk_commit():
        with world.connect() as conn:
            state = {"savepoint": False, "failed": False}
            real_cursor = conn.cursor

            class Cur:
                def __init__(self, cur):
                    self._c = cur

                def __getattr__(self, k):
                    return getattr(self._c, k)

                def __iter__(self):
                    return iter(self._c)

                def execute(self, sql, *a):
                    if isinstance(sql, str) and sql.startswith("SAVEPOINT"):
                        state["savepoint"] = True
                    return self._c.execute(sql, *a)

            class Conn:
                def __getattr__(self, k):
                    return getattr(conn, k)

                def cursor(self, *a, **k):
                    return Cur(real_cursor(*a, **k))

                def commit(self):
                    if state["savepoint"] and not state["failed"]:
                        state["failed"] = True
                        conn.rollback()
                        raise RuntimeError("commit blew up")
                    return conn.commit()

            yield Conn()

    res = observer.capture_session(candidates=cands, final_signals=finals, guard_decisions=decisions,
                                   guards_evaluated=True, session_date=world.rows["S000"][-1]["date"],
                                   strategy=world.strategy, connect=failing_first_chunk_commit, chunk_size=3)
    assert res.profile["chunk_retries"] == 1
    assert res.status == "complete" and res.counters["captured"] == 6 and res.counters["already_captured"] == 0
    assert table(world, "SELECT count(*) FROM candidate_observation") == [(6,)]
    assert len(res.links) == 6
