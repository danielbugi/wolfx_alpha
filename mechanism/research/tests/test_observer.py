"""Candidate capture against a throwaway Postgres schema: the funnel, first-valid-write-wins, session integrity,
never-raises. The immutability triggers, ON CONFLICT semantics and CHECK constraints are the real ones."""
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from conftest import ROOT  # noqa: F401
from research import observer, registry, repository
from screeners.signal_ledger_writer import StrategyRef
from test_snapshot_builder import make_rows

COUNTER_IDENTITY = ("captured", "already_captured", "stale_skipped", "snapshot_skipped", "invalid_skipped")


# ----------------------------------------------------------------------------------------------- world
class World:
    """stock_prices + daily_fundamentals in the throwaway schema, and candidate builders that agree with them."""

    def __init__(self, connect, strategy_id):
        self.connect = connect
        self.strategy = StrategyRef(strategy_id, "donchian_breakout", "v1")
        self.session = None
        self.rows = {}

    def create_tables(self):
        with self.connect() as c:
            cur = c.cursor()
            cur.execute("CREATE TABLE stock_prices (symbol VARCHAR(10), date DATE, open NUMERIC, high NUMERIC, "
                        "low NUMERIC, close NUMERIC, volume BIGINT)")
            cur.execute("CREATE TABLE daily_fundamentals (symbol VARCHAR(10), date DATE, sector VARCHAR(60))")
            c.commit()

    def activate(self, state="enabled", effective_from="2000-01-01", strategy_id=None):
        with self.connect() as c:
            c.cursor().execute("SELECT research_capture_set_state(%s, %s, %s, 'activation boundary for tests')",
                               (strategy_id or self.strategy.id, state, effective_from))
            c.commit()

    def add_prices(self, symbol, rows):
        self.rows[symbol] = rows
        with self.connect() as c:
            cur = c.cursor()
            cur.executemany(
                "INSERT INTO stock_prices VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(symbol, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows])
            c.commit()

    def add_sector(self, symbol, sector, on):
        with self.connect() as c:
            c.cursor().execute("INSERT INTO daily_fundamentals VALUES (%s, %s, %s)", (symbol, on, sector))
            c.commit()

    def candidate(self, symbol, signal_type="bullish_breakout", screening_date=None, **kw):
        last = self.rows[symbol][-1]
        close = float(last["close"])
        bullish = "bullish" in signal_type
        sig = {
            "symbol": symbol, "signal_type": signal_type, "screening_date": screening_date or last["date"],
            "current_price": close,
            "prev_donchian_high": close * (0.97 if bullish else 1.20),
            "prev_donchian_low": close * (0.80 if bullish else 1.03),
            "donchian_high": close, "donchian_low": close * 0.8, "distance_to_breakout": 1.5,
            "atr_14": 2.0, "urgency": "immediate", "screener_defaults": [],
            "alignment_score": 70.0, "alignment_grade": "B",
            "weekly_context": {"weekly_trend": "bullish", "week_ending_date": date(2024, 11, 15)},
            "monthly_context": None,
        }
        sig.update(kw)
        return sig


@pytest.fixture
def world(schema_env, seed):
    _, connect = schema_env
    w = World(connect, seed.strategy_id())
    seed.conn.commit()
    w.create_tables()
    w.activate()
    return w


def final_of(c, **kw):
    f = dict(c)
    f.update(combined_score=66.0, ml_prediction_available=True, ml_momentum_probability=0.61,
             ml_confidence="medium", ml_model_version="m_test")
    f.update(kw)
    return f


def run(world, candidates, finals=None, decisions=None, evaluated=True, session=None, connect=None):
    return observer.capture_session(
        candidates=candidates, final_signals=finals if finals is not None else [], guard_decisions=decisions or {},
        guards_evaluated=evaluated, session_date=session or world.rows[candidates[0]["symbol"]][-1]["date"],
        strategy=world.strategy, connect=connect or world.connect)


def table(world, sql, params=None):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def assert_identity(res):
    assert res.counters["candidates"] == sum(res.counters[k] for k in COUNTER_IDENTITY), res.counters


def three_symbol_world(world):
    for i, s in enumerate(("AAA", "BBB", "CCC")):
        world.add_prices(s, make_rows(seed=10 + i))
    world.add_sector("AAA", "Technology", date(2024, 11, 10))
    a = world.candidate("AAA")
    b = world.candidate("BBB", "near_bearish")
    c = world.candidate("CCC")
    return a, b, c


# ------------------------------------------------------------------------------------------------- flag
@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("TRUE", True), (" on ", True),
                                            ("0", False), ("", False), ("no", False), (None, False)])
def test_capture_is_default_off(value, expected):
    env = {} if value is None else {observer.ENABLE_ENV: value}
    assert observer.is_enabled(env) is expected


# ------------------------------------------------------------------------------------------ happy path
def test_every_candidate_is_recorded_with_its_funnel_outcome(world):
    a, b, c = three_symbol_world(world)
    fa = final_of(a)
    decisions = {"AAA": [], "BBB": [], "CCC": ["illiquid_dollar_volume"]}
    # BBB passed the guards but got no ML score (a near-breakout: Grade B here, no probability)
    fb = final_of(b, ml_prediction_available=False, ml_momentum_probability=None)
    res = run(world, [a, b, c], finals=[fa, fb], decisions=decisions)

    assert res.status == "complete" and res.error is None
    assert_identity(res)
    assert res.counters["captured"] == 3 and res.counters["guard_rejected"] == 1
    assert set(res.links) == {("AAA", 1), ("BBB", -1), ("CCC", 1)}

    rows = {r[0]: r for r in table(world, "SELECT symbol, direction, passed_guard, guard_reasons, ml_status, "
                                          "ml_score, tracked_intent, session_rank, triggered, signal_type "
                                          "FROM candidate_observation")}
    assert rows["AAA"][2] is True and rows["AAA"][3] is None
    assert rows["AAA"][4] == "scored" and float(rows["AAA"][5]) == pytest.approx(0.61)
    assert rows["AAA"][6] is True and rows["AAA"][7] == 1 and rows["AAA"][8] is True
    assert rows["BBB"][1] == -1 and rows["BBB"][8] is False                    # near-breakout, bearish
    assert rows["BBB"][4] == "not_processed" and rows["BBB"][5] is None         # never 0
    assert rows["BBB"][6] is False                                              # near-breakouts are not tracked
    assert rows["BBB"][7] == 2
    assert rows["CCC"][2] is False and rows["CCC"][3] == ["illiquid_dollar_volume"]
    assert rows["CCC"][6] is False and rows["CCC"][7] is None                   # dropped before ranking

    run_row = table(world, "SELECT status, candidates, captured, guard_rejected, session_date FROM candidate_capture_run")
    assert run_row == [("complete", 3, 3, 1, world.rows["AAA"][-1]["date"])]


def test_snapshot_comes_from_prices_and_carries_the_sector_and_its_age(world):
    a, b, c = three_symbol_world(world)
    run(world, [a, b, c])
    (snap,) = table(world, "SELECT bar_date, close, sector, sector_source, sector_asof, snapshot_status "
                           "FROM feature_snapshot WHERE symbol = 'AAA'")
    assert snap[0] == world.rows["AAA"][-1]["date"] and float(snap[1]) == pytest.approx(world.rows["AAA"][-1]["close"])
    assert snap[2:5] == ("Technology", "daily_fundamentals", date(2024, 11, 10)) and snap[5] == "complete"
    (none,) = table(world, "SELECT sector, sector_source, sector_asof FROM feature_snapshot WHERE symbol = 'BBB'")
    assert none == (None, None, None)                                           # unknown stays NULL, never 'Unknown'


def test_breakout_distance_uses_the_measured_atr_not_the_screeners_default(world):
    world.add_prices("AAA", make_rows(seed=3))
    cand = world.candidate("AAA", atr_14=0.0001, screener_defaults=["atr_14"])
    res = run(world, [cand], finals=[final_of(cand)], decisions={"AAA": []})
    assert res.counters["defaulted_flagged"] == 1
    (row,) = table(world, "SELECT breakout_dist_atr, tracked_intent, screener_defaults FROM candidate_observation")
    assert table(world, "SELECT atr_source FROM candidate_observation") == [("fallback",)]
    snap_atr = float(table(world, "SELECT features->>'atr_14' FROM feature_snapshot")[0][0])
    expected = (float(cand["current_price"]) - cand["prev_donchian_high"]) / snap_atr
    assert float(row[0]) == pytest.approx(expected, rel=1e-4)                  # not (price - high) / 0.0001
    assert row[1] is False                                                      # the ledger refuses a defaulted ATR
    assert row[2] == ["atr_14"]


def test_two_strategies_share_one_snapshot(world):
    world.add_prices("AAA", make_rows(seed=3))
    cand = world.candidate("AAA")
    run(world, [cand], decisions={"AAA": []})
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO strategies (strategy_key, strategy_version) VALUES ('second', 'v1') RETURNING id")
        second = cur.fetchone()[0]
        c.commit()
    other = StrategyRef(second, "second", "v1")
    world.activate(strategy_id=second)
    res = observer.capture_session(candidates=[cand], final_signals=[], guard_decisions={"AAA": []},
                                   guards_evaluated=True, session_date=world.rows["AAA"][-1]["date"],
                                   strategy=other, connect=world.connect)
    assert res.counters["captured"] == 1
    assert table(world, "SELECT count(*) FROM feature_snapshot") == [(1,)]
    assert table(world, "SELECT count(*), count(DISTINCT snapshot_id) FROM candidate_observation") == [(2, 1)]


# ------------------------------------------------------------------------- first valid write wins
def test_rerun_is_a_noop_and_returns_the_same_links(world):
    a, b, c = three_symbol_world(world)
    first = run(world, [a, b, c], decisions={"AAA": [], "BBB": [], "CCC": []})
    before = table(world, "SELECT id, capture_hash, captured_at FROM candidate_observation ORDER BY id")
    second = run(world, [a, b, c], decisions={"AAA": [], "BBB": [], "CCC": []})
    assert second.counters["captured"] == 0 and second.counters["already_captured"] == 3
    assert second.counters["hash_drift"] == 0 and second.counters["snapshot_drift"] == 0
    assert second.links == first.links
    assert table(world, "SELECT id, capture_hash, captured_at FROM candidate_observation ORDER BY id") == before
    assert_identity(second)


def test_a_different_rerun_is_counted_as_drift_and_never_applied(world):
    a, b, c = three_symbol_world(world)
    run(world, [a], decisions={"AAA": []})
    (before,) = table(world, "SELECT id, passed_guard, capture_hash FROM candidate_observation")
    res = run(world, [a], decisions={"AAA": ["illiquid_dollar_volume"]})
    assert res.counters["already_captured"] == 1 and res.counters["hash_drift"] == 1
    assert res.counters["captured"] == 0
    assert table(world, "SELECT id, passed_guard, capture_hash FROM candidate_observation") == [before]
    assert res.links[("AAA", 1)].observation_id == before[0]


# -------------------------------------------------------------------------------- session integrity
def test_a_candidate_from_another_session_is_skipped_never_relabelled(world):
    world.add_prices("AAA", make_rows(seed=3))
    session = world.rows["AAA"][-1]["date"]
    stale = world.candidate("AAA", screening_date=session - timedelta(days=1))
    res = run(world, [stale], session=session)
    assert res.counters["stale_skipped"] == 1 and res.counters["captured"] == 0
    assert "AAA" in res.skipped and res.links == {}
    assert table(world, "SELECT count(*) FROM candidate_observation") == [(0,)]
    assert table(world, "SELECT count(*) FROM feature_snapshot") == [(0,)]
    assert_identity(res)


def test_a_symbol_whose_prices_stop_before_the_session_is_not_snapshotted(world):
    rows = make_rows(seed=3)
    world.add_prices("AAA", rows[:-1])                      # newest bar is the day before the session
    session = rows[-1]["date"]
    cand = world.candidate("AAA", screening_date=session)   # the screener claims the session bar
    res = run(world, [cand], session=session)
    assert res.counters["stale_skipped"] == 1 and res.counters["captured"] == 0
    assert table(world, "SELECT count(*) FROM candidate_observation") == [(0,)]


def test_bars_after_the_session_never_enter_the_snapshot(world):
    rows = make_rows(seed=3, n=330)
    world.add_prices("AAA", rows)
    session = rows[319]["date"]                              # a replay of an earlier session
    cand = world.candidate("AAA", screening_date=session)
    cand["current_price"] = float(rows[319]["close"])
    res = run(world, [cand], session=session)
    assert res.counters["captured"] == 1
    (bar, close) = table(world, "SELECT bar_date, close FROM feature_snapshot")[0]
    assert bar == session and float(close) == pytest.approx(rows[319]["close"])


def test_a_price_that_disagrees_with_the_stored_bar_is_invalid_not_recorded(world):
    world.add_prices("AAA", make_rows(seed=3))
    cand = world.candidate("AAA", current_price=999.0)
    res = run(world, [cand])
    assert res.counters["invalid_skipped"] == 1 and "price_mismatch" in res.skipped["AAA"]
    assert table(world, "SELECT count(*) FROM candidate_observation") == [(0,)]
    assert_identity(res)


def test_unknown_signal_type_is_invalid(world):
    world.add_prices("AAA", make_rows(seed=3))
    res = run(world, [world.candidate("AAA", signal_type="sideways")])
    assert res.counters["invalid_skipped"] == 1
    assert_identity(res)


# ------------------------------------------------------------------------- guards unavailable / failure
def test_guards_not_evaluated_is_null_not_true(world):
    world.add_prices("AAA", make_rows(seed=3))
    res = run(world, [world.candidate("AAA")], evaluated=False)
    assert res.counters["guard_not_evaluated"] == 1
    assert table(world, "SELECT passed_guard, guard_reasons FROM candidate_observation") == [(None, None)]


def test_a_symbol_missing_from_the_decisions_is_not_assumed_to_pass(world):
    world.add_prices("AAA", make_rows(seed=3))
    run(world, [world.candidate("AAA")], decisions={"OTHER": []}, evaluated=True)
    assert table(world, "SELECT passed_guard FROM candidate_observation") == [(None,)]


def test_a_missing_session_is_refused_without_raising(world):
    world.add_prices("AAA", make_rows(seed=3))
    res = observer.capture_session(candidates=[world.candidate("AAA")], final_signals=[], guard_decisions={},
                                   guards_evaluated=False, session_date=None, strategy=world.strategy,
                                   connect=world.connect)
    assert res.status == "failed" and "explicit session_date" in res.error
    assert table(world, "SELECT count(*) FROM candidate_capture_run") == [(0,)]


def test_database_failure_never_raises(world):
    world.add_prices("AAA", make_rows(seed=3))

    @contextmanager
    def broken():
        raise ConnectionError("db down")
        yield  # pragma: no cover

    res = run(world, [world.candidate("AAA")], connect=broken)
    assert res.status == "failed" and "db down" in res.error and res.links == {}


def test_one_bad_candidate_does_not_stop_the_others(world, monkeypatch):
    a, b, c = three_symbol_world(world)
    real = repository.insert_observation

    def flaky(cur, row):
        if row["symbol"] == "BBB":
            raise RuntimeError("boom")
        return real(cur, row)

    monkeypatch.setattr(repository, "insert_observation", flaky)
    res = run(world, [a, b, c], decisions={"AAA": [], "BBB": [], "CCC": []})
    assert res.status == "partial"                       # a candidate was lost: the run says so, it is not "complete"
    assert res.counters["captured"] == 2 and res.counters["invalid_skipped"] == 1
    assert "boom" in res.skipped["BBB"]
    assert {r[0] for r in table(world, "SELECT symbol FROM candidate_observation")} == {"AAA", "CCC"}
    assert table(world, "SELECT count(*) FROM feature_snapshot WHERE symbol = 'BBB'") == [(0,)]  # rolled back with it
    assert_identity(res)


def test_a_manifest_mismatch_refuses_capture_and_records_the_failed_run(world, monkeypatch):
    world.add_prices("AAA", make_rows(seed=3))
    cand = world.candidate("AAA")
    assert run(world, [cand], decisions={"AAA": []}).status == "complete"
    feats = list(registry._FEATURES)
    feats[0] = ("atr_15",) + feats[0][1:]
    monkeypatch.setattr(registry, "_FEATURES", feats)
    other_session = world.rows["AAA"][-1]["date"] + timedelta(days=1)
    res = run(world, [cand], session=other_session)
    assert res.status == "failed" and "new feature_set_version" in res.error
    assert table(world, "SELECT status FROM candidate_capture_run ORDER BY id") == [("complete",), ("failed",)]
    assert table(world, "SELECT count(*) FROM candidate_observation") == [(1,)]


def test_the_capture_run_counts_what_was_skipped(world):
    a, b, c = three_symbol_world(world)
    stale = world.candidate("BBB", "near_bearish", screening_date=a["screening_date"] - timedelta(days=1))
    res = run(world, [a, stale], decisions={"AAA": []}, session=a["screening_date"])
    (status, stale_n, skipped) = table(world, "SELECT status, stale_skipped, skipped_symbols FROM candidate_capture_run")[0]
    assert status == "complete" and stale_n == 1 and "BBB" in skipped
    assert_identity(res)
