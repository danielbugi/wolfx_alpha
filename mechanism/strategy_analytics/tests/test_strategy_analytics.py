"""strategy_analytics: pure definitions/state logic, plus real-Postgres tests of every query (skip when
unreachable, like mechanism/screeners/tests). Each DB test runs inside its own throwaway strategy row,
so counts are exact regardless of what else is in the database; price bars use 2099 dates so the
test controls the latest market session."""
import math
import os
import sys
import uuid
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from strategy_analytics import analytics as A  # noqa: E402
from strategy_analytics import definitions as D  # noqa: E402


# =================================================================== pure
def test_metric_states_never_turn_nothing_into_zero():
    assert D.metric(None, 0) == {"value": None, "n": 0, "state": "no_data"}
    assert D.metric(0.5, 0)["value"] is None  # a value with no sample is dropped, not shown
    assert D.metric(0.0, 3) == {"value": 0.0, "n": 3, "state": "preliminary"}
    assert D.metric(0.25, D.MIN_SAMPLE_SIZE)["state"] == "ok"
    assert D.ratio(0, 0) == {"value": None, "n": 0, "state": "no_data"}
    na = D.not_available("x", "B")
    assert na["state"] == "not_available" and na["value"] is None and na["release"] == "B"


def test_lifecycle():
    assert D.lifecycle("open", None) == "open"
    assert D.lifecycle("open", "split_suspect") == "held"
    for s in D.TERMINAL_STATUSES:
        assert D.lifecycle(s, None) == "resolved"


def _row(signal_date, ref, lag=0, capped=False, invalid=False, last_eval=None):
    return {"signal_date": signal_date, "reference_session": ref, "lag_sessions": lag, "lag_capped": capped,
            "invalid_bar": invalid, "last_evaluated_date": last_eval or signal_date}


def test_price_data_state_is_measured_in_sessions_not_calendar_days():
    ref = date(2099, 7, 10)
    assert A._price_data_state(_row(ref, ref)) == "awaiting_first_session"
    assert A._price_data_state(_row(ref - timedelta(days=5), ref, lag=0)) == "current"  # e.g. over a weekend
    assert A._price_data_state(_row(ref - timedelta(days=5), ref, lag=D.STALE_AFTER_SESSIONS - 1)) == "lagging"
    assert A._price_data_state(_row(ref - timedelta(days=5), ref, lag=D.STALE_AFTER_SESSIONS)) == "stale"
    assert A._price_data_state(_row(ref - timedelta(days=90), ref, lag=1, capped=True)) == "stale"
    assert A._price_data_state(_row(ref, None)) == "no_price_data"


def test_evaluation_state():
    ref = date(2099, 7, 10)
    assert A._evaluation_state(_row(ref - timedelta(days=3), ref, last_eval=ref)) == "up_to_date"
    assert A._evaluation_state(_row(ref - timedelta(days=3), ref)) == "pending"
    assert A._evaluation_state(_row(ref - timedelta(days=3), ref, invalid=True, last_eval=ref)) == "invalid_price_blocked"


def _never_called(*_a, **_k):
    raise AssertionError("must validate before querying")


@pytest.mark.parametrize("kwargs", [
    {"sort": "best"}, {"direction": "long"}, {"status": "won"}, {"lifecycle_state": "closed"},
    {"evaluation_flag": "bad"}, {"resolution_flag": "bad"}, {"limit": 0}, {"limit": A.MAX_PAGE_SIZE + 1},
    {"offset": -1},
])
def test_list_signals_rejects_unknown_input_before_touching_sql(kwargs):
    with pytest.raises(ValueError):
        A.list_signals(_never_called, {"id": 1}, **kwargs)


def test_aggregate_zero_fills_a_direction_with_no_rows():
    total = {"is_total": 1, "direction": None, "signals": 2, "open_total": 2, "normally_open": 2, "held": 0,
             "resolved": 0, "winners": 0, "stopped": 0, "target1": 0, "target2": 0, "target3": 0,
             "expired": 0, "ambiguous": 0, "avg_r": None, "median_r": None, "expired_avg_r": None,
             "avg_bars": None, "median_bars": None, "avg_mae_r": None, "first_session": None,
             "latest_session": None, "this_week": 0, "this_month": 0}
    bull = {**total, "is_total": 0, "direction": 1}
    agg = A._aggregate(lambda *_: [total, bull], 1, None)
    assert agg["bullish"]["signals"] == 2
    assert agg["bearish"]["signals"] == 0 and agg["bearish"]["avg_r"] is None
    assert A._direction_block(agg["bearish"])["win_rate"]["state"] == "no_data"


# =================================================================== real Postgres
@pytest.fixture(scope="module")
def conn():
    try:
        import psycopg2
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        c = psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
                             dbname=os.getenv("DB_NAME", "trading_production"),
                             user=os.getenv("DB_USER", "trading_user"), password=os.getenv("DB_PASSWORD", ""),
                             connect_timeout=3)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT evaluation_flag FROM signal_ledger LIMIT 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger lacks migration 21: {type(e).__name__}")
    yield c
    c.close()


@pytest.fixture
def fetch(conn):
    from psycopg2.extras import RealDictCursor

    def _fetch(sql, params=None):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()] if cur.description else []
    return _fetch


class Ledger:
    """A throwaway strategy plus helpers to add signals and price bars; everything is deleted after."""

    def __init__(self, fetch):
        self.fetch = fetch
        self.key = "zz_si_" + uuid.uuid4().hex[:8]
        fetch("INSERT INTO strategies (strategy_key, strategy_version, description) VALUES (%s, 'v1', 'test') "
              "RETURNING id", (self.key,))
        self.strategy = A.get_strategy(fetch, self.key, "v1")
        self.symbols = set()

    def sym(self):
        s = "ZS" + uuid.uuid4().hex[:6].upper()
        self.symbols.add(s)
        return s

    def signal(self, symbol=None, signal_date=date(2099, 7, 1), direction=1, status="open", outcome_r=None,
               bars_held=None, resolution_flag=None, evaluation_flag=None, last_evaluated_date=None,
               grade="A", sector="Technology", model_version=None, mae_r=None, strategy_version="v1",
               entry=100.0, atr=2.0):
        symbol = symbol or self.sym()
        self.symbols.add(symbol)
        r = 2 * atr
        resolved_date = signal_date + timedelta(days=bars_held or 1) if status != "open" else None
        rows = self.fetch("""
            INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price,
                target1_price, target2_price, target3_price, sector, quality_grade, status, outcome_r, mae_r,
                resolved_date, bars_held, last_evaluated_date, strategy_id, strategy_version, model_version,
                resolution_flag, evaluation_flag)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (symbol, signal_date, direction, entry, atr, entry - direction * r, entry + direction * r,
             entry + direction * 2 * r, entry + direction * 3 * r, sector, grade, status, outcome_r, mae_r,
             resolved_date, bars_held, last_evaluated_date or signal_date, self.strategy["id"], strategy_version,
             model_version, resolution_flag, evaluation_flag))
        return rows[0]["id"]

    def bar(self, symbol, d, high=101.0, low=99.0, close=100.0):
        self.symbols.add(symbol)
        self.fetch("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) "
                   "VALUES (%s, %s, %s, %s, %s, %s, 1000)", (symbol, d, close, high, low, close))

    def cleanup(self):
        self.fetch("DELETE FROM signal_ledger WHERE strategy_id = %s", (self.strategy["id"],))
        if self.symbols:
            self.fetch("DELETE FROM stock_prices WHERE symbol = ANY(%s)", (list(self.symbols),))
        self.fetch("DELETE FROM strategies WHERE id = %s", (self.strategy["id"],))


@pytest.fixture
def ledger(fetch):
    lg = Ledger(fetch)
    yield lg
    lg.cleanup()


# ------------------------------------------------------------------- summary / performance
def test_empty_strategy_reports_nothing_not_zero(fetch, ledger):
    s = A.summary(fetch, ledger.strategy)
    t, p = s["tracking"], s["performance"]
    assert t["tracking_status"] == "no_signals_yet" and t["total_signals"] == 0
    assert t["first_tracked_session"] is None and t["latest_tracked_session"] is None
    for m in ("win_rate", "average_r", "median_r", "average_holding_bars", "stop_rate"):
        assert p[m] == {"value": None, "n": 0, "state": "no_data"}
    assert p["average_mfe_r"]["state"] == "not_available"
    assert s["directions"]["bullish"]["signals"] == 0 and s["directions"]["bearish"]["win_rate"]["state"] == "no_data"
    assert s["capabilities"]["candidate_observations"] == {"available": False, "release": "B"}


def test_all_open_strategy(fetch, ledger):
    ledger.signal(direction=1)
    ledger.signal(direction=1)
    ledger.signal(direction=-1)
    s = A.summary(fetch, ledger.strategy)
    t = s["tracking"]
    assert (t["total_signals"], t["open"], t["normally_open"], t["held"], t["resolved"]) == (3, 3, 3, 0, 0)
    assert (t["bullish"], t["bearish"]) == (2, 1)
    assert t["first_tracked_session"] == t["latest_tracked_session"] == date(2099, 7, 1)
    assert s["performance"]["win_rate"]["state"] == "no_data" and s["performance"]["win_rate"]["value"] is None
    assert s["performance"]["resolved_n"] == 0


def test_mixed_outcomes_follow_the_canonical_definitions(fetch, ledger):
    ledger.signal(status="target1", outcome_r=1.0, bars_held=3, mae_r=0.2)
    ledger.signal(status="target3", outcome_r=3.0, bars_held=1, mae_r=0.0)
    ledger.signal(status="stopped", outcome_r=-1.0, bars_held=2, mae_r=1.0)
    ledger.signal(status="stopped", outcome_r=-1.0, bars_held=1, mae_r=1.0,
                  resolution_flag="same_bar_stop_and_target")
    ledger.signal(status="expired", outcome_r=0.4, bars_held=20, mae_r=0.5, direction=-1)
    ledger.signal(status="expired", outcome_r=-0.2, bars_held=20, mae_r=0.6, direction=-1)
    ledger.signal()                                  # normally open
    ledger.signal(evaluation_flag="split_suspect")   # held
    s = A.summary(fetch, ledger.strategy)
    t, p, o = s["tracking"], s["performance"], s["outcomes"]

    assert (t["total_signals"], t["open"], t["normally_open"], t["held"], t["resolved"]) == (8, 2, 1, 1, 6)
    assert (p["resolved_n"], p["winners"], p["stopped"], p["expired"], p["ambiguous"]) == (6, 2, 2, 2, 1)
    # held and open are excluded from every rate's denominator; the ambiguous stop counts as a loss
    assert p["win_rate"] == {"value": round(2 / 6, 4), "n": 6, "state": "ok"}
    # a positive-R expiry is not a winner
    assert p["average_r"]["value"] == pytest.approx(round(2.2 / 6, 3))
    assert p["median_r"]["value"] == pytest.approx(0.1)          # median of -1,-1,-0.2,0.4,1,3
    assert p["average_holding_bars"]["value"] == pytest.approx(round(47 / 6, 1))
    assert p["median_holding_bars"]["value"] == pytest.approx(2.5)
    assert p["stop_rate"]["value"] == pytest.approx(round(2 / 6, 4))
    assert p["average_mae_r"]["value"] == pytest.approx(round(3.3 / 6, 3))

    assert o["terminal"]["target1"]["count"] == 1 and o["terminal"]["target2"]["count"] == 0
    assert o["terminal"]["expired"]["average_r"] == {"value": pytest.approx(0.1), "n": 2, "state": "preliminary"}
    # the +0.4R expiry is not a win, but it is visible as a positive expiry and counted in average R
    exp = o["terminal"]["expired"]
    assert (exp["positive_count"], exp["negative_count"], exp["flat_count"]) == (1, 1, 0)
    assert exp["positive_share"]["value"] == 0.5
    assert (p["expired_positive"], p["expired_negative"]) == (1, 1)
    assert o["ambiguous"]["count"] == 1 and o["ambiguous"]["share_of_stopped"]["value"] == 0.5
    assert {k: v["count"] for k, v in o["target_milestones"].items()} == {
        "reached_target1": 2, "reached_target2": 1, "reached_target3": 1}

    bull, bear = s["directions"]["bullish"], s["directions"]["bearish"]
    assert (bull["signals"], bull["resolved"], bull["winners"], bull["held"], bull["normally_open"]) == (6, 4, 2, 1, 1)
    assert bull["win_rate"] == {"value": 0.5, "n": 4, "state": "preliminary"}
    assert (bear["signals"], bear["resolved"], bear["winners"], bear["expired"]) == (2, 2, 0, 2)
    assert bear["win_rate"] == {"value": 0.0, "n": 2, "state": "preliminary"}  # measured zero, not "no data"
    assert bear["average_r"]["value"] == pytest.approx(0.1)


def test_single_resolved_signal_is_preliminary(fetch, ledger):
    ledger.signal(status="target2", outcome_r=2.0, bars_held=4)
    p = A.summary(fetch, ledger.strategy)["performance"]
    assert p["win_rate"] == {"value": 1.0, "n": 1, "state": "preliminary"}
    assert p["average_r"] == {"value": 2.0, "n": 1, "state": "preliminary"}


def test_this_week_and_month_are_relative_to_the_latest_market_session(fetch, ledger):
    ref = date(2099, 7, 15)  # a Wednesday
    ledger.bar(ledger.sym(), ref)
    week_start = ref - timedelta(days=ref.weekday())
    ledger.signal(signal_date=ref)
    ledger.signal(signal_date=week_start)
    ledger.signal(signal_date=week_start - timedelta(days=1))   # previous week, same month
    ledger.signal(signal_date=date(2099, 6, 30))                # previous month
    t = A.summary(fetch, ledger.strategy)["tracking"]
    assert (t["signals_this_week"], t["signals_this_month"]) == (2, 3)
    assert A.summary(fetch, ledger.strategy)["reference_session"] == ref


def test_overall_performance_matches_summary(fetch, ledger):
    ledger.signal(status="target1", outcome_r=1.0, bars_held=2)
    ledger.signal(status="stopped", outcome_r=-1.0, bars_held=1)
    ledger.signal()
    s = A.summary(fetch, ledger.strategy)["performance"]
    o = A.overall_performance(fetch, ledger.strategy["id"])
    assert o["performance"] == s
    assert o["by_status"] == {"stopped": 1, "target1": 1}
    assert o["counts"]["open_total"] == 1


# ------------------------------------------------------------------- data health
S = [date(2099, 8, d) for d in (3, 4, 5, 6, 7, 10)]  # six market sessions (skips a weekend)


def _calendar(ledger):
    cal = ledger.sym()
    for d in S:
        ledger.bar(cal, d)


def test_data_health_healthy(fetch, ledger):
    _calendar(ledger)
    sym = ledger.sym()
    for d in S:
        ledger.bar(sym, d)
    ledger.signal(symbol=sym, signal_date=S[2], last_evaluated_date=S[-1])
    h = A.data_health(fetch, ledger.strategy)
    assert h["status"] == "healthy" and h["issues"] == []
    assert h["market_data"]["reference_session"] == S[-1]
    assert h["market_data"]["open_signals_by_price_data_state"]["current"] == 1
    assert h["evaluation"]["open_signals_by_evaluation_state"]["up_to_date"] == 1
    assert all(v == 0 for v in h["invariants"].values())


def test_data_health_classifies_waiting_lagging_stale_missing_and_invalid(fetch, ledger):
    _calendar(ledger)
    waiting = ledger.signal(signal_date=S[-1])                         # on the latest session: legitimately waiting
    lag_sym = ledger.sym()
    for d in S[:-1]:
        ledger.bar(lag_sym, d)
    ledger.signal(symbol=lag_sym, signal_date=S[1], last_evaluated_date=S[-2])  # 1 session behind -> lagging, pending
    missing = ledger.sym()
    ledger.bar(missing, S[0])
    ledger.signal(symbol=missing, signal_date=S[0])                    # no bar since signal: 5 sessions -> stale
    bad = ledger.sym()
    for d in S:
        ledger.bar(bad, d, low=0.0 if d == S[3] else 99.0)            # zero low on the path
    ledger.signal(symbol=bad, signal_date=S[2], last_evaluated_date=S[2])

    h = A.data_health(fetch, ledger.strategy)
    ps = h["market_data"]["open_signals_by_price_data_state"]
    es = h["evaluation"]["open_signals_by_evaluation_state"]
    assert ps["awaiting_first_session"] == 1 and ps["lagging"] == 1 and ps["stale"] == 1 and ps["current"] == 1
    assert es["invalid_price_blocked"] == 1 and es["pending"] == 2 and es["up_to_date"] == 1
    assert h["market_data"]["open_signals_without_forward_bar"] == 1
    assert h["status"] == "attention"
    by_symbol = {a["symbol"]: a for a in h["attention"]["open_signals"]}
    assert by_symbol[missing]["lag_sessions"] == 5 and by_symbol[missing]["price_data_state"] == "stale"
    assert by_symbol[lag_sym]["lag_sessions"] == 1
    assert by_symbol[bad]["evaluation_state"] == "invalid_price_blocked"
    assert waiting not in [a["id"] for a in h["attention"]["open_signals"]]


def test_data_health_held_and_invariant_violations(fetch, ledger):
    _calendar(ledger)
    held = ledger.signal(evaluation_flag="split_suspect", last_evaluated_date=S[3], signal_date=S[1])
    ledger.signal(strategy_version="v9")                                # lineage mismatch
    ledger.signal(status="open", outcome_r=1.0)                         # open row carrying an outcome
    ledger.signal(status="stopped", outcome_r=-1.0, bars_held=1, model_version="m1",
                  resolution_flag="same_bar_stop_and_target")
    h = A.data_health(fetch, ledger.strategy)
    assert h["ledger"]["held"] == 1 and h["ledger"]["held_by_flag"] == {"split_suspect": 1}
    assert h["ledger"]["ambiguous_resolutions"] == 1
    assert h["attention"]["held_signals"][0]["id"] == held
    assert h["attention"]["held_signals"][0]["held_since"] == S[3]
    assert h["invariants"]["strategy_version_mismatch"] == 1
    assert h["invariants"]["open_with_outcome"] == 1
    assert h["invariants"]["duplicate_event_identities"] == 0
    assert h["status"] == "violation"
    assert h["coverage"]["model_version"]["count"] == 1 and h["coverage"]["model_version"]["total"] == 4
    assert h["coverage"]["feature_snapshot_id"]["collected"] is False
    assert h["evaluation"]["evaluator_runs"]["state"] == "not_available"


def test_invalid_bar_sql_matches_the_evaluator_rule(fetch, ledger):
    """INVALID_BAR_PREDICATE must agree with evaluate_signal_ledger._invalid_bar_reason on every case."""
    from screeners.evaluate_signal_ledger import _invalid_bar_reason
    sym = ledger.sym()
    cases = [(101, 99, 100), (101, 0, 100), (101, -1, 100), (0, 99, 100), (101, 99, 0), (None, 99, 100),
             (101, None, 100), (101, 99, None), ("NaN", 99, 100), (99, 101, 100), (101, 99, 102),
             (101, 99, 98), (100, 100, 100)]
    for i, (h, lo, c) in enumerate(cases):
        ledger.fetch("INSERT INTO stock_prices (symbol, date, high, low, close, volume) VALUES (%s,%s,%s,%s,%s,1)",
                     (sym, date(2099, 9, 1) + timedelta(days=i), h, lo, c))
    rows = fetch(f"SELECT high, low, close, {A.INVALID_BAR_PREDICATE} AS invalid FROM stock_prices sp "
                 "WHERE symbol = %s ORDER BY date", (sym,))
    for r in rows:
        py = [None if r[k] is None else float(r[k]) for k in ("high", "low", "close")]
        assert r["invalid"] == (_invalid_bar_reason(*py) is not None), (py, r["invalid"])
    assert any(r["high"] is not None and math.isnan(float(r["high"])) for r in rows)


# ------------------------------------------------------------------- signal explorer
def test_pagination_is_stable_and_complete(fetch, ledger):
    ids = [ledger.signal(signal_date=date(2099, 7, 1) + timedelta(days=i % 3)) for i in range(7)]
    pages = [A.list_signals(fetch, ledger.strategy, limit=3, offset=o) for o in (0, 3, 6)]
    assert [len(p["items"]) for p in pages] == [3, 3, 1]
    assert all(p["total"] == 7 for p in pages)
    assert [p["has_more"] for p in pages] == [True, True, False]
    seen = [i["id"] for p in pages for i in p["items"]]
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 7
    keys = [(i["signal_date"], i["id"]) for p in pages for i in p["items"]]
    assert keys == sorted(keys, reverse=True)  # newest: signal_date DESC, id DESC
    assert A.list_signals(fetch, ledger.strategy, offset=50)["items"] == []


def test_filters(fetch, ledger):
    s1 = ledger.sym()
    ledger.signal(symbol=s1, grade="A", sector="Energy", model_version="m1")
    ledger.signal(direction=-1, grade="C")
    ledger.signal(evaluation_flag="split_suspect")
    ledger.signal(status="stopped", outcome_r=-1.0, bars_held=1, resolution_flag="same_bar_stop_and_target")
    ledger.signal(status="target2", outcome_r=2.0, bars_held=5, signal_date=date(2099, 6, 1))
    L = A.list_signals
    st = ledger.strategy
    assert L(fetch, st, symbol=s1.lower())["total"] == 1
    assert L(fetch, st, direction="bearish")["total"] == 1
    assert L(fetch, st, lifecycle_state="open")["total"] == 2
    assert L(fetch, st, lifecycle_state="held")["items"][0]["lifecycle"] == "held"
    assert L(fetch, st, lifecycle_state="resolved")["total"] == 2
    assert L(fetch, st, status="target2")["items"][0]["is_winner"] is True
    assert L(fetch, st, resolution_flag="same_bar_stop_and_target")["items"][0]["is_winner"] is False
    assert L(fetch, st, evaluation_flag="split_suspect")["total"] == 1
    assert L(fetch, st, date_to=date(2099, 6, 30))["total"] == 1
    assert L(fetch, st, date_from=date(2099, 7, 1))["total"] == 4
    assert L(fetch, st, quality_grade="c")["total"] == 1
    assert L(fetch, st, sector="Energy")["total"] == 1
    assert L(fetch, st, model_version="m1")["total"] == 1
    assert L(fetch, st, lifecycle_state="open")["items"][0]["is_winner"] is None


def test_sorts(fetch, ledger):
    a = ledger.signal(status="target3", outcome_r=3.0, bars_held=2, grade="B")
    b = ledger.signal(status="stopped", outcome_r=-1.0, bars_held=9, grade="A")
    c = ledger.signal(grade="C")  # open: no R, no holding period -> always last
    ids = lambda sort: [i["id"] for i in A.list_signals(fetch, ledger.strategy, sort=sort)["items"]]  # noqa: E731
    assert ids("r_desc") == [a, b, c]
    assert ids("r_asc") == [b, a, c]
    assert ids("holding_desc") == [b, a, c]
    assert ids("grade") == [b, a, c]


def test_signal_detail(fetch, ledger):
    sym = ledger.sym()
    for d in S[:3]:
        ledger.bar(sym, d)
    sid = ledger.signal(symbol=sym, signal_date=S[0], status="target1", outcome_r=1.0, bars_held=2, mae_r=0.3,
                        entry=50.0, atr=1.5)
    d = A.get_signal(fetch, ledger.strategy, sid)
    assert d["identity"] == {"symbol": sym, "signal_date": S[0], "direction": "bullish", "strategy_version": "v1"}
    assert d["trade_plan"]["risk_per_share"] == 3.0 and d["trade_plan"]["risk_pct_of_entry"] == 0.06
    assert d["lifecycle"]["state"] == "resolved" and d["lifecycle"]["is_winner"] is True
    assert d["lifecycle"]["forward_bars_available"] == 2 and d["lifecycle"]["symbol_latest_bar"] == S[2]
    assert d["provenance"]["observation_id"] is None and d["provenance"]["release_b_lineage"] is False
    assert d["context"]["feature_set_version"] is None
    assert [e["event"] for e in d["timeline"]] == ["signal", "target1"]

    held = ledger.signal(evaluation_flag="split_suspect", last_evaluated_date=S[1], signal_date=S[0])
    assert [e["event"] for e in A.get_signal(fetch, ledger.strategy, held)["timeline"]] == ["signal", "held:split_suspect"]
    assert A.get_signal(fetch, ledger.strategy, 10 ** 12) is None


def test_signal_detail_is_scoped_to_its_strategy(fetch, ledger):
    other = Ledger(fetch)
    try:
        foreign = other.signal()
        assert A.get_signal(fetch, ledger.strategy, foreign) is None
        assert A.list_signals(fetch, ledger.strategy)["total"] == 0
    finally:
        other.cleanup()


def test_list_strategies_includes_tracking(fetch, ledger):
    ledger.signal()
    ledger.signal(status="expired", outcome_r=0.1, bars_held=20)
    mine = [s for s in A.list_strategies(fetch) if s["key"] == ledger.key][0]
    assert mine["tracking"]["total_signals"] == 2 and mine["tracking"]["open"] == 1
    assert mine["tracking"]["resolved"] == 1 and mine["display_name"] == ledger.key
    assert A.get_strategy(fetch, ledger.key, "v2") is None
