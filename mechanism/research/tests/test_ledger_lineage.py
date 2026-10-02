"""signal_ledger_writer with the research lineage, in a throwaway schema: the link columns are filled from the
observer's links, a late link fills NULLs but never overwrites, a defaulted ATR is refused, and the shared
`ledger_ineligibility` predicate is what both the writer and the observer's `tracked_intent` use."""
from datetime import date, timedelta

import pytest

from conftest import ROOT  # noqa: F401
from screeners import signal_ledger_writer as slw
from screeners.signal_ledger_writer import ObservationLink, StrategyRef, ledger_ineligibility, write_signals

SESSION = date(2099, 1, 15)


class SchemaDB:
    """The two methods the writer uses, on a connection pinned to the throwaway schema."""

    def __init__(self, connect):
        self.connect = connect

    def execute_dict_query(self, sql, params=None):
        from psycopg2.extras import RealDictCursor
        with self.connect() as c:
            cur = c.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            c.commit()
            return [dict(r) for r in rows]

    def execute_insert(self, sql, params=None):
        with self.connect() as c:
            c.cursor().execute(sql, params)
            c.commit()
            return True


@pytest.fixture
def db(connect, seed):
    seed.conn.commit()
    return SchemaDB(connect)


@pytest.fixture
def strategy(seed):
    sid = seed.strategy_id()
    seed.conn.commit()
    return StrategyRef(sid, "donchian_breakout", "v1")


def sig(symbol="AAA", signal_type="bullish_breakout", **kw):
    base = {"symbol": symbol, "signal_type": signal_type, "screening_date": SESSION, "current_price": 100.0,
            "atr_14": 2.0, "sector": "Technology", "quality_grade": "B", "screener_defaults": []}
    base.update(kw)
    return base


def ledger(db):
    return db.execute_dict_query("SELECT symbol, direction, observation_id, feature_snapshot_id, feature_set_version "
                                 "FROM signal_ledger ORDER BY symbol, direction")


def real_link(seed, symbol="AAA", direction=1):
    snap = seed.snapshot(symbol, SESSION)
    obs = seed.observation(snapshot_id=snap, symbol=symbol, direction=direction)
    seed.conn.commit()
    return ObservationLink(obs, snap, "t0_v1")


# ------------------------------------------------------------------------------------------ lineage
def test_lineage_columns_are_written_from_the_links(db, strategy, seed):
    link = real_link(seed)
    assert write_signals(db, [sig()], SESSION, strategy, {("AAA", 1): link}) == 1
    assert ledger(db) == [{"symbol": "AAA", "direction": 1, "observation_id": link.observation_id,
                           "feature_snapshot_id": link.feature_snapshot_id, "feature_set_version": "t0_v1"}]


def test_no_link_writes_null_lineage_exactly_as_before(db, strategy):
    assert write_signals(db, [sig()], SESSION, strategy) == 1
    (row,) = ledger(db)
    assert row["observation_id"] is None and row["feature_snapshot_id"] is None and row["feature_set_version"] is None


def test_a_link_for_the_other_direction_is_not_used(db, strategy, seed):
    link = real_link(seed, direction=-1)
    write_signals(db, [sig()], SESSION, strategy, {("AAA", -1): link})
    assert ledger(db)[0]["observation_id"] is None


def test_a_late_link_fills_nulls_but_a_stored_link_is_never_overwritten(db, strategy, seed):
    write_signals(db, [sig()], SESSION, strategy)                      # first run: capture was off
    link = real_link(seed)
    write_signals(db, [sig()], SESSION, strategy, {("AAA", 1): link})  # re-run with capture on
    assert ledger(db)[0]["observation_id"] == link.observation_id

    other_snap = seed.snapshot("ZZZ", SESSION)
    other = ObservationLink(seed.observation(snapshot_id=other_snap, symbol="ZZZ"), other_snap, "t0_v1")
    seed.conn.commit()
    write_signals(db, [sig()], SESSION, strategy, {("AAA", 1): other})
    assert ledger(db)[0]["observation_id"] == link.observation_id      # first valid write wins


def test_two_strategies_hold_independent_rows_for_the_same_symbol_and_session(db, strategy, seed):
    cur = seed.conn.cursor()
    cur.execute("INSERT INTO strategies (strategy_key, strategy_version) VALUES ('second', 'v1') RETURNING id")
    second = StrategyRef(cur.fetchone()[0], "second", "v1")
    seed.conn.commit()
    write_signals(db, [sig()], SESSION, strategy)
    write_signals(db, [sig()], SESSION, second)
    rows = db.execute_dict_query("SELECT strategy_id FROM signal_ledger WHERE symbol = 'AAA' ORDER BY strategy_id")
    assert [r["strategy_id"] for r in rows] == sorted([strategy.id, second.id])


# ------------------------------------------------------------------------------ eligibility predicate
def test_a_defaulted_atr_is_refused_not_written(db, strategy):
    assert write_signals(db, [sig(screener_defaults=["atr_14"]), sig("BBB")], SESSION, strategy) == 1
    assert [r["symbol"] for r in ledger(db)] == ["BBB"]


def test_non_strict_reproduces_the_pre_boundary_writer_and_writes_a_defaulted_atr_row(db, strategy):
    assert write_signals(db, [sig(screener_defaults=["atr_14"]), sig("BBB")], SESSION, strategy, strict=False) == 2
    assert sorted(r["symbol"] for r in ledger(db)) == ["AAA", "BBB"]


@pytest.mark.parametrize("signal,expected", [
    (sig(), None),
    (sig(signal_type="near_bullish"), "not_a_tracked_type"),
    (sig(screening_date=SESSION - timedelta(days=1)), "not_the_session_bar"),
    (sig(screening_date=None), "not_the_session_bar"),
    (sig(screener_defaults=["rsi_14", "atr_14"]), "defaulted_atr"),
    (sig(screener_defaults=["rsi_14"]), None),                  # other defaults do not touch the trade plan
    (sig(atr_14=None), "missing_price_or_atr"),
    (sig(atr_14="x"), "missing_price_or_atr"),
    (sig(atr_14=0), "non_positive_price_or_atr"),
    (sig(current_price=-1), "non_positive_price_or_atr"),
])
def test_ledger_ineligibility(signal, expected):
    assert ledger_ineligibility(signal, SESSION) == expected


def test_a_datetime_screening_date_is_the_same_session():
    from datetime import datetime
    assert ledger_ineligibility(sig(screening_date=datetime(2099, 1, 15, 0, 0)), SESSION) is None


def test_a_signal_from_another_session_is_never_written_under_this_one(db, strategy):
    assert write_signals(db, [sig(screening_date=SESSION - timedelta(days=1))], SESSION, strategy) == 0
    assert ledger(db) == []


def test_resolve_default_strategy_returns_the_seeded_ref(db, strategy):
    ref = slw._resolve_default_strategy(db)
    assert ref == strategy
