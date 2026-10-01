"""The screener's research-capture hook: default off, never changes the screening result or the ledger write,
never raises, and hands the observer the PRE-guard candidates plus the guard verdicts. No network, no database."""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

SESSION = date(2026, 9, 29)


@pytest.fixture
def mts(monkeypatch):
    try:
        from screeners import multi_timeframe_screener as m
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"screener module not importable here: {type(e).__name__}: {e}")
    monkeypatch.setattr(m, "ML_AVAILABLE", False)
    return m


def sig(symbol, grade="A", alignment=80, signal_type="bullish_breakout"):
    return {"symbol": symbol, "signal_type": signal_type, "screening_date": SESSION, "alignment_grade": grade,
            "alignment_score": alignment, "current_price": 10.0, "atr_14": 1.0}


def make(mts, monkeypatch, signals, survivors):
    s = mts.MultiTimeframeMLScreener(target_session=SESSION)
    s.ml_enhancer = None

    def guards(sigs):
        s.guard_decisions = {x["symbol"]: ([] if x["symbol"] in survivors else ["illiquid_dollar_volume"])
                             for x in sigs}
        s.guards_evaluated = True
        return [x for x in sigs if x["symbol"] in survivors]

    monkeypatch.setattr(s, "get_multi_timeframe_signals", lambda: signals)
    monkeypatch.setattr(s, "_apply_universe_guards", guards)
    monkeypatch.setattr(s, "_create_enhanced_results", lambda a, b: {"summary": {"total_signals": len(a)}})
    monkeypatch.setattr(s, "save_results", lambda r: None)
    return s


@pytest.fixture
def ledger(monkeypatch):
    from screeners import signal_ledger_writer as slw
    seen = {}
    monkeypatch.setattr(slw, "write_todays_signals",
                        lambda db, sigs, session, links=None: seen.update(symbols=[x["symbol"] for x in sigs],
                                                                          session=session, links=links))
    return seen


def test_capture_is_off_by_default_and_the_ledger_gets_no_links(mts, monkeypatch, ledger):
    monkeypatch.delenv("RESEARCH_CAPTURE_ENABLED", raising=False)
    from research import observer
    monkeypatch.setattr(observer, "capture_session", lambda **k: pytest.fail("capture ran while disabled"))
    s = make(mts, monkeypatch, [sig("AAA"), sig("BBB")], {"AAA", "BBB"})
    s.screen_all_symbols()
    assert not s.failed and ledger["links"] == {} and ledger["symbols"] == ["AAA", "BBB"]


def test_enabled_capture_receives_pre_guard_candidates_and_the_verdicts(mts, monkeypatch, ledger):
    monkeypatch.setenv("RESEARCH_CAPTURE_ENABLED", "1")
    from research import observer
    from screeners import signal_ledger_writer as slw
    monkeypatch.setattr(slw, "_resolve_default_strategy", lambda db: slw.StrategyRef(1, "donchian_breakout", "v1"))
    got = {}
    link = slw.ObservationLink(5, 6, "t0_v1")

    def fake_capture(**kw):
        got.update(kw)
        return observer.CaptureResult("complete", 1, {("KEEP", 1): link})

    monkeypatch.setattr(observer, "capture_session", fake_capture)
    s = make(mts, monkeypatch, [sig("KEEP"), sig("DROP", alignment=99)], {"KEEP"})
    s.screen_all_symbols()
    assert not s.failed
    assert [c["symbol"] for c in got["candidates"]] == ["KEEP", "DROP"]            # BEFORE the guards
    assert [c["symbol"] for c in got["final_signals"]] == ["KEEP"]                  # after guards, in final order
    assert got["guard_decisions"] == {"KEEP": [], "DROP": ["illiquid_dollar_volume"]}
    assert got["guards_evaluated"] is True and got["session_date"] == SESSION
    assert ledger["links"] == {("KEEP", 1): link} and ledger["session"] == SESSION


def test_a_capture_that_raises_changes_nothing_downstream(mts, monkeypatch, ledger):
    monkeypatch.setenv("RESEARCH_CAPTURE_ENABLED", "1")
    from research import observer
    from screeners import signal_ledger_writer as slw
    monkeypatch.setattr(slw, "_resolve_default_strategy", lambda db: slw.StrategyRef(1, "donchian_breakout", "v1"))

    def boom(**kw):
        raise RuntimeError("capture exploded")

    monkeypatch.setattr(observer, "capture_session", boom)
    s = make(mts, monkeypatch, [sig("AAA")], {"AAA"})
    results = s.screen_all_symbols()
    assert not s.failed and results["summary"]["total_signals"] == 1
    assert ledger["symbols"] == ["AAA"] and ledger["links"] == {}


def test_a_failed_capture_result_yields_no_links_but_the_ledger_still_writes(mts, monkeypatch, ledger):
    monkeypatch.setenv("RESEARCH_CAPTURE_ENABLED", "1")
    from research import observer
    from screeners import signal_ledger_writer as slw
    monkeypatch.setattr(slw, "_resolve_default_strategy", lambda db: slw.StrategyRef(1, "donchian_breakout", "v1"))
    monkeypatch.setattr(observer, "capture_session", lambda **k: observer.CaptureResult("failed", error="x"))
    s = make(mts, monkeypatch, [sig("AAA")], {"AAA"})
    s.screen_all_symbols()
    assert ledger["symbols"] == ["AAA"] and ledger["links"] == {}


def test_capture_import_failure_is_swallowed(mts, monkeypatch, ledger):
    monkeypatch.setenv("RESEARCH_CAPTURE_ENABLED", "1")
    import research
    monkeypatch.delattr(research, "observer", raising=False)
    monkeypatch.setitem(sys.modules, "research.observer", None)   # makes `from research import observer` fail
    s = make(mts, monkeypatch, [sig("AAA")], {"AAA"})
    s.screen_all_symbols()
    assert not s.failed and ledger["symbols"] == ["AAA"]
