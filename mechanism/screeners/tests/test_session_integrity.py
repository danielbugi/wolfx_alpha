"""Session integrity at the screener and the ledger boundary (2026-10-01). No network, no database.

A signal belongs to the session of the bar it was computed on. The pipeline tells the screener which session
it is processing; a symbol whose newest bar is another day is rejected with a reason, never relabelled, and the
ledger writer refuses it again as defense in depth.

Run:  python -m pytest mechanism/screeners/tests -q      (from the repo root)
"""
import os
import sys
from datetime import date, datetime

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from screeners import signal_ledger_writer as slw  # noqa: E402
from shared import session_integrity as si  # noqa: E402

MON, TUE, WED = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)


# ------------------------------------------------------------------ the pure helper
def test_classify():
    assert si.classify(TUE, TUE) is None
    assert si.classify(MON, TUE) == si.STALE_BAR
    assert si.classify(WED, TUE) == si.FUTURE_BAR
    assert si.classify(None, TUE) == si.UNREADABLE_DATE
    assert si.classify("garbage", TUE) == si.UNREADABLE_DATE


def test_classify_coerces_datetime_and_iso_string_but_never_guesses():
    assert si.classify(datetime(2026, 9, 29, 21, 0), TUE) is None
    assert si.classify("2026-09-29", TUE) is None
    assert si.classify("2026-09-28", TUE) == si.STALE_BAR


def test_partition_keeps_order_and_returns_accepted_untouched():
    a, b, c = ({"symbol": "A", "screening_date": TUE}, {"symbol": "B", "screening_date": MON},
               {"symbol": "C", "screening_date": TUE})
    accepted, rejected = si.partition_by_session([a, b, c], TUE)
    assert accepted == [a, c] and accepted[0] is a
    assert rejected == [(b, si.STALE_BAR)]
    assert b["screening_date"] == MON, "a rejected record must not be relabelled"


def test_summarize_rejections_is_log_friendly():
    _, rej = si.partition_by_session([{"symbol": s, "screening_date": MON} for s in "ABC"], TUE)
    assert si.summarize_rejections(rej).startswith("stale_bar=3 (A, B, C)")
    assert si.summarize_rejections([]) == "none"


# ------------------------------------------------------------------ ledger writer (fake db)
class FakeDB:
    def __init__(self):
        self.inserted = []

    def execute_insert(self, query, params=None):
        self.inserted.append(params)
        return True


@pytest.fixture
def ledger(monkeypatch):
    monkeypatch.setattr(slw, "_resolve_default_strategy", lambda db: slw.StrategyRef(1, "donchian_breakout", "v1"))
    monkeypatch.setattr(slw, "_has_open_position", lambda *a, **k: False)
    return FakeDB()


def sig(symbol, screening_date, signal_type="bullish_breakout"):
    return {"symbol": symbol, "signal_type": signal_type, "screening_date": screening_date,
            "current_price": 100.0, "atr_14": 2.0}


def test_ledger_rejects_a_signal_dated_before_the_pipeline_session(ledger):
    n = slw.write_todays_signals(ledger, [sig("OLD", MON)], TUE)       # pipeline session Tue, signal Mon
    assert n == 0 and ledger.inserted == []


def test_ledger_rejects_a_signal_dated_after_the_pipeline_session(ledger):
    assert slw.write_todays_signals(ledger, [sig("NEW", WED)], TUE) == 0 and ledger.inserted == []


def test_ledger_rejects_a_missing_or_unreadable_screening_date(ledger):
    s = sig("X", None)
    del s["screening_date"]
    assert slw.write_todays_signals(ledger, [s, sig("Y", "not-a-date")], TUE) == 0
    assert ledger.inserted == []


def test_ledger_writes_only_the_matching_signals_of_a_mixed_batch_under_their_own_date(ledger):
    n = slw.write_todays_signals(ledger, [sig("OK1", TUE), sig("STALE", MON), sig("OK2", TUE)], TUE)
    assert n == 2
    assert [p["symbol"] for p in ledger.inserted] == ["OK1", "OK2"]
    assert {p["signal_date"] for p in ledger.inserted} == {TUE}


def test_ledger_accepts_a_datetime_screening_date_on_the_session_day(ledger):
    assert slw.write_todays_signals(ledger, [sig("DT", datetime(2026, 9, 29, 0, 0))], TUE) == 1


def test_untracked_near_signals_are_ignored_not_reported_as_rejections(ledger, caplog):
    slw.write_todays_signals(ledger, [sig("N", MON, "near_bullish")], TUE)
    assert ledger.inserted == [] and "REJECTED" not in caplog.text


# ------------------------------------------------------------------ the screener (fake db, no ML)
@pytest.fixture
def screener_module(monkeypatch):
    try:
        from screeners import multi_timeframe_screener as mts
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"screener module not importable here: {type(e).__name__}: {e}")
    monkeypatch.setattr(mts, "ML_AVAILABLE", False)
    return mts


class Row(dict):
    def __missing__(self, key):
        return None


def candidate_row(symbol, screening_date):
    """A bullish breakout on `screening_date`'s bar."""
    return Row(symbol=symbol, screening_date=screening_date, current_price=105.0, prev_close=99.0,
               donchian_high_20=104.0, donchian_low_20=90.0, prev_donchian_high=100.0, prev_donchian_low=90.0,
               atr_14=2.0, volume=1000)


class QueryDB:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def execute_dict_query(self, query, params=None):
        self.queries.append((query, params))
        return self.rows


def make_screener(mts, monkeypatch, rows, target):
    monkeypatch.setattr(mts, "db", QueryDB(rows))
    return mts.MultiTimeframeMLScreener(target_session=target)


def test_screener_query_is_bounded_by_the_explicit_session_not_current_date(screener_module, monkeypatch):
    s = make_screener(screener_module, monkeypatch, [candidate_row("AAA", TUE)], TUE)
    s.get_daily_breakout_signals()
    query, params = screener_module.db.queries[0]
    assert params == {"target": TUE}
    assert "CURRENT_DATE" not in query and "%(target)s" in query


def test_screener_keeps_target_session_signals(screener_module, monkeypatch):
    s = make_screener(screener_module, monkeypatch, [candidate_row("AAA", TUE)], TUE)
    out = s.get_daily_breakout_signals()
    assert [(x["symbol"], x["screening_date"]) for x in out] == [("AAA", TUE)]
    assert not s.failed


def test_screener_rejects_a_stale_symbol_instead_of_relabelling_it(screener_module, monkeypatch):
    rows = [candidate_row("FRESH", TUE), candidate_row("STALE", MON)]
    s = make_screener(screener_module, monkeypatch, rows, TUE)
    out = s.get_daily_breakout_signals()
    assert [x["symbol"] for x in out] == ["FRESH"]
    assert s.stale_rejected == 1 and not s.failed
    assert all(x["screening_date"] == TUE for x in out)


def test_screener_fails_when_no_symbol_holds_the_target_session(screener_module, monkeypatch):
    s = make_screener(screener_module, monkeypatch, [candidate_row("A", MON), candidate_row("B", MON)], TUE)
    assert s.get_daily_breakout_signals() == []
    assert s.failed, "all-stale data must fail the run (pipeline exit 1), not quietly screen Monday's bars"


def test_screener_without_a_session_and_an_empty_calendar_refuses_to_screen(screener_module, monkeypatch):
    monkeypatch.setattr(screener_module.market_calendar, "resolve_session", lambda *a, **k: (None, "empty"))
    s = make_screener(screener_module, monkeypatch, [candidate_row("AAA", TUE)], None)
    assert s.get_daily_breakout_signals() == [] and s.failed


def test_screen_all_symbols_writes_the_ledger_under_the_explicit_session_not_the_first_signal(screener_module, monkeypatch):
    mts = screener_module
    s = make_screener(mts, monkeypatch, [], TUE)
    stale_first = [sig("STALE", MON), sig("FRESH", TUE)]       # the old code took all_signals[0]['screening_date'] = Mon
    for x in stale_first:
        x.setdefault("alignment_grade", "A")
    monkeypatch.setattr(s, "get_multi_timeframe_signals", lambda: stale_first)
    monkeypatch.setattr(s, "_create_enhanced_results", lambda a, b: {"summary": {"total_signals": len(a)}})
    monkeypatch.setattr(s, "save_results", lambda r: None)
    seen = {}
    monkeypatch.setattr(slw, "write_todays_signals", lambda db, signals, session, links=None, strict=True: seen.update(session=session))
    s.screen_all_symbols()
    assert seen["session"] == TUE
