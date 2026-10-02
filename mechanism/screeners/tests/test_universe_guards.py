"""Universe guards + ML-score merge in the multi-timeframe screener (Commit B). No network, no database.

The guards reuse the exact functions the digest trusts (price_features / digest_builder) so a symbol the
channel excludes can no longer reach the dashboard or the signal ledger. These tests pin each rule's
boundary so a refactor (Release B's per-symbol extraction) cannot silently move it.

Run:  python -m pytest mechanism/screeners/tests/test_universe_guards.py -q      (from the repo root)
"""
import inspect
import os
import sys
from datetime import date, timedelta

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
    if not m.GUARDS_AVAILABLE:
        pytest.skip("universe guards not importable here")
    monkeypatch.setattr(m, "ML_AVAILABLE", False)
    # These tests exercise the guards themselves, so put every session on the guarded side of the boundary
    # (gating is covered by test_guards_effective_from.py).
    monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", "2000-01-01")
    return m


# ------------------------------------------------------------------ synthetic price history
def bars(symbol, n, *, price=100.0, volume=20_000.0, end=SESSION, overrides=None):
    """n consecutive calendar-day bars ending on `end`. dollar volume = price * volume per bar.
    overrides: {index_from_start: dict(field=value)} to shape individual bars (index n-1 is the last)."""
    out = []
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        out.append({"symbol": symbol, "date": d, "open": price, "high": price * 1.01, "low": price * 0.99,
                    "close": price, "volume": volume})
        if overrides and i in overrides:
            out[-1].update(overrides[i])
    return out


class PriceDB:
    """Answers the guard's one query, honouring the symbol list and the `date <= target` bound."""

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def execute_dict_query(self, query, params=None):
        self.calls.append((query, params))
        syms, target = set(params["symbols"]), params["target"]
        return sorted((r for r in self.rows if r["symbol"] in syms and r["date"] <= target),
                      key=lambda r: (r["symbol"], r["date"]))


def screener(mts, monkeypatch, rows, target=SESSION):
    db = PriceDB(rows)
    monkeypatch.setattr(mts, "db", db)
    return mts.MultiTimeframeMLScreener(target_session=target), db


def sig(symbol, signal_type="bullish_breakout", grade="A", alignment=80):
    return {"symbol": symbol, "signal_type": signal_type, "screening_date": SESSION,
            "alignment_grade": grade, "alignment_score": alignment}


def survivors(mts, monkeypatch, rows, symbols, target=SESSION):
    s, _ = screener(mts, monkeypatch, rows, target)
    return [x["symbol"] for x in s._apply_universe_guards([sig(sym) for sym in symbols])]


# ------------------------------------------------------------------ >= 60 bars
def test_min_bars_is_the_digests_60(mts):
    assert mts.dbld.MIN_BARS == 60


def test_fewer_than_60_bars_is_dropped_and_exactly_60_passes(mts, monkeypatch):
    rows = bars("SHORT", 59) + bars("EXACT", 60)
    assert survivors(mts, monkeypatch, rows, ["SHORT", "EXACT"]) == ["EXACT"]


def test_a_symbol_with_no_price_rows_at_all_is_dropped_not_passed(mts, monkeypatch):
    assert survivors(mts, monkeypatch, bars("OTHER", 80), ["GHOST"]) == []


# ------------------------------------------------------------------ prior-20-session dollar volume
def test_dollar_volume_floor_is_one_million(mts):
    assert mts.pf.MIN_DOLLAR_VOLUME_20 == 1_000_000.0


def test_exactly_one_million_prior_20_session_dollar_volume_passes(mts, monkeypatch):
    rows = bars("EDGE", 80, price=100.0, volume=10_000.0)           # 100 * 10,000 = $1.0M
    assert survivors(mts, monkeypatch, rows, ["EDGE"]) == ["EDGE"]


def test_just_below_the_floor_is_dropped(mts, monkeypatch):
    rows = bars("THIN", 80, price=100.0, volume=9_999.0)
    assert survivors(mts, monkeypatch, rows, ["THIN"]) == []


def test_floor_uses_the_prior_20_sessions_so_a_breakout_day_volume_spike_cannot_qualify(mts, monkeypatch):
    # Prior 20 sessions at $0.5M; the signal day itself trades $100M. Counting the signal day would pass it.
    rows = bars("SPIKE", 80, price=100.0, volume=5_000.0, overrides={79: {"volume": 1_000_000.0}})
    assert survivors(mts, monkeypatch, rows, ["SPIKE"]) == []


def test_floor_uses_the_prior_20_sessions_so_a_quiet_signal_day_does_not_disqualify(mts, monkeypatch):
    # Prior 20 sessions at $2M; the signal day itself trades almost nothing. Counting it would still pass
    # (the mean of 20 stays above $1M) so also make the window sensitive: the oldest window bar is excluded.
    rows = bars("QUIET", 80, price=100.0, volume=20_000.0, overrides={79: {"volume": 1.0}})
    assert survivors(mts, monkeypatch, rows, ["QUIET"]) == ["QUIET"]


def test_the_window_is_exactly_the_20_bars_before_the_last(mts, monkeypatch):
    # 80 bars -> last is index 79, window is indices 59..78 (the 20 sessions before the signal day).
    def one_bar_makes_the_mean_pass(idx):
        # 19 bars at $0.95M plus one bar at $1.95M -> mean $1.0M exactly passes; without it the mean is $0.95M.
        return bars("W", 80, price=100.0, volume=9_500.0, overrides={idx: {"volume": 19_500.0}})
    for inside in (59, 78):
        assert survivors(mts, monkeypatch, one_bar_makes_the_mean_pass(inside), ["W"]) == ["W"]
    for outside in (58, 79):                         # one bar too old / the signal day itself
        assert survivors(mts, monkeypatch, one_bar_makes_the_mean_pass(outside), ["W"]) == []


# ------------------------------------------------------------------ discontinuity window
def test_lookback_is_253_bars(mts):
    assert mts.pf.LOOKBACK_BARS == 253


def jump_at(symbol, n, pos):
    """A 4x price jump (ratio > 3) at bar `pos`, volume scaled so dollar volume stays well above the floor."""
    over = {i: {"open": 400.0, "high": 404.0, "low": 396.0, "close": 400.0} for i in range(pos, n)}
    return bars(symbol, n, price=100.0, volume=50_000.0, overrides=over)


def test_discontinuity_inside_the_253_bar_window_drops_the_symbol(mts, monkeypatch):
    n = 320
    last = n - 1
    assert survivors(mts, monkeypatch, jump_at("INSIDE", n, last - 253), ["INSIDE"]) == []   # oldest bar still in window
    assert survivors(mts, monkeypatch, jump_at("RECENT", n, last - 5), ["RECENT"]) == []
    assert survivors(mts, monkeypatch, jump_at("TODAY", n, last), ["TODAY"]) == []


def test_discontinuity_just_outside_the_window_passes(mts, monkeypatch):
    n = 320
    assert survivors(mts, monkeypatch, jump_at("OLD", n, (n - 1) - 254), ["OLD"]) == ["OLD"]


def test_a_clean_long_history_passes(mts, monkeypatch):
    assert survivors(mts, monkeypatch, bars("CLEAN", 320, volume=50_000.0), ["CLEAN"]) == ["CLEAN"]


def test_a_non_positive_or_inconsistent_bar_in_the_window_drops_the_symbol(mts, monkeypatch):
    bad = bars("OHLC", 120, volume=50_000.0, overrides={100: {"high": 90.0, "low": 110.0}})   # high < low
    assert survivors(mts, monkeypatch, bad, ["OHLC"]) == []


# ------------------------------------------------------------------ session-bounded read, mixed batches
def test_the_guard_reads_prices_only_up_to_the_explicit_session(mts, monkeypatch):
    # A later bar already in the table (a rerun of an older session) must not leak into the decision:
    # with it counted the last bar would be the future one and dv20 would be taken from the wrong window.
    history = bars("RERUN", 80, price=100.0, volume=5_000.0)                       # $0.5M -> fails the floor
    future = bars("RERUN", 25, price=100.0, volume=500_000.0, end=SESSION + timedelta(days=25))
    s, db = screener(mts, monkeypatch, history + future)
    assert s._apply_universe_guards([sig("RERUN")]) == []
    query, params = db.calls[0]
    assert params["target"] == SESSION and "date <= %(target)s" in query


def test_a_mixed_batch_keeps_input_order_and_only_the_passing_symbols(mts, monkeypatch):
    rows = bars("AAA", 80, volume=50_000.0) + bars("BBB", 30, volume=50_000.0) + bars("CCC", 80, volume=50_000.0)
    s, _ = screener(mts, monkeypatch, rows)
    signals = [sig("CCC"), sig("BBB"), sig("AAA", "bearish_breakout")]
    out = s._apply_universe_guards(signals)
    assert [(x["symbol"], x["signal_type"]) for x in out] == [("CCC", "bullish_breakout"), ("AAA", "bearish_breakout")]
    assert out[0] is signals[0], "survivors are passed through untouched, never rewritten"


def test_both_directions_of_one_symbol_share_the_symbol_level_decision(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, bars("DUO", 80, volume=50_000.0))
    out = s._apply_universe_guards([sig("DUO"), sig("DUO", "near_bearish")])
    assert len(out) == 2


def test_guard_is_a_noop_for_an_empty_signal_list_and_makes_no_query(mts, monkeypatch):
    s, db = screener(mts, monkeypatch, [])
    assert s._apply_universe_guards([]) == [] and db.calls == []


def test_requested_but_unavailable_guards_fail_the_run_rather_than_pass_unfiltered(mts, monkeypatch):
    monkeypatch.setattr(mts, "GUARDS_AVAILABLE", False)
    s, db = screener(mts, monkeypatch, [])
    with pytest.raises(RuntimeError, match="GUARDS_EFFECTIVE_FROM"):
        s._apply_universe_guards([sig("ANY")])
    assert db.calls == []


def test_unavailable_guards_are_irrelevant_while_inert(mts, monkeypatch):
    monkeypatch.setattr(mts, "GUARDS_AVAILABLE", False)
    monkeypatch.delenv("GUARDS_EFFECTIVE_FROM")
    s, db = screener(mts, monkeypatch, [])
    signals = [sig("ANY")]
    assert s._apply_universe_guards(signals) is signals and db.calls == []


# ------------------------------------------------------------------ ML-unprocessed + combined score
class EnhancerStub:
    ml_model_version = "momentum_test_v1"


def test_a_signal_ml_never_processed_gets_the_no_ml_combined_score_and_honest_flags(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, [])
    s.ml_enhancer = None
    out = s._merge_ml_scores([sig("D1", grade="D", alignment=50)], [])
    row = out[0]
    assert row["combined_score"] == 30.0                     # alignment * 0.6, ml contribution 0
    assert row["ml_confidence"] == "not_processed"
    assert row["ml_prediction_available"] is False
    assert row["ml_momentum_probability"] is None            # never a fabricated probability
    assert row["ml_model_version"] is None                   # no enhancer -> no invented version


def test_ml_model_version_is_recorded_when_an_enhancer_exists_but_skipped_the_signal(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, [])
    s.ml_enhancer = EnhancerStub()
    out = s._merge_ml_scores([sig("D1", grade="D", alignment=50)], [])
    assert out[0]["ml_model_version"] == "momentum_test_v1" and out[0]["ml_confidence"] == "not_processed"


def test_a_missing_or_none_alignment_score_means_zero_contribution_not_a_crash(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, [])
    a = sig("NOAL", grade="F")
    a.pop("alignment_score")
    b = sig("NONE", grade="F", alignment=None)
    assert [r["combined_score"] for r in s._merge_ml_scores([a, b], [])] == [0.0, 0.0]


def test_ml_enhanced_copies_win_over_the_fallback_and_are_passed_through_unchanged(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, [])
    s.ml_enhancer = EnhancerStub()
    enhanced = dict(sig("A1"), combined_score=91.5, ml_confidence="high", ml_prediction_available=True)
    out = s._merge_ml_scores([sig("A1"), sig("D1", grade="D", alignment=40)], [enhanced])
    assert out[0] is enhanced and out[0]["combined_score"] == 91.5
    assert out[1]["combined_score"] == 24.0 and out[1]["ml_confidence"] == "not_processed"


def test_the_merge_key_is_symbol_and_type_so_the_other_direction_is_not_swallowed(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, [])
    enhanced = dict(sig("BOTH", "bullish_breakout"), combined_score=88.0)
    out = s._merge_ml_scores([sig("BOTH", "bullish_breakout"), sig("BOTH", "bearish_breakout")], [enhanced])
    assert out[0]["combined_score"] == 88.0
    assert out[1]["ml_confidence"] == "not_processed" and out[1]["combined_score"] == 48.0


def test_unscored_copies_from_the_ml_unavailable_or_failed_path_get_the_fallback_score(mts, monkeypatch):
    # screen_all_symbols passes the un-enhanced Grade A/B/C signals as `ml_enhanced_signals` when ML is
    # unavailable or raised; those carry no combined_score and must not be mistaken for ML output.
    s, _ = screener(mts, monkeypatch, [])
    s.ml_enhancer = EnhancerStub()
    a = sig("A1", grade="A", alignment=80)
    out = s._merge_ml_scores([a], [dict(a)])
    assert out[0]["combined_score"] == 48.0 and out[0]["ml_confidence"] == "not_processed"
    assert out[0]["ml_prediction_available"] is False


def test_the_merge_never_mutates_its_inputs(mts, monkeypatch):
    s, _ = screener(mts, monkeypatch, [])
    original = sig("D1", grade="D", alignment=50)
    snapshot = dict(original)
    s._merge_ml_scores([original], [])
    assert original == snapshot


# ------------------------------------------------------------------ end to end: guard -> merge -> order -> ledger
def run_screen(mts, monkeypatch, signals, enhanced_by_symbol=None, guard_survivors=None):
    from screeners import signal_ledger_writer as slw
    s, _ = screener(mts, monkeypatch, [])
    s.ml_enhancer = EnhancerStub() if enhanced_by_symbol else None
    monkeypatch.setattr(s, "get_multi_timeframe_signals", lambda: signals)
    keep = guard_survivors if guard_survivors is not None else {x["symbol"] for x in signals}
    monkeypatch.setattr(s, "_apply_universe_guards", lambda sigs: [x for x in sigs if x["symbol"] in keep])
    if enhanced_by_symbol:
        def fake_enhance(high_quality):
            return [dict(x, combined_score=enhanced_by_symbol[x["symbol"]]) for x in high_quality]
        monkeypatch.setattr(s, "enhance_signals_with_ml", fake_enhance)
    seen = {}
    monkeypatch.setattr(s, "_create_enhanced_results",
                        lambda a, b: seen.update(results_order=[x["symbol"] for x in a]) or {"summary": {"total_signals": len(a)}})
    monkeypatch.setattr(s, "save_results", lambda r: None)
    monkeypatch.setattr(slw, "write_todays_signals",
                        lambda db, sigs, session, links=None, strict=True: seen.update(
                            ledger_order=[x["symbol"] for x in sigs], session=session, strict=strict))
    s.screen_all_symbols()
    assert not s.failed
    return seen


def test_final_order_is_best_combined_score_first_across_ml_and_non_ml_signals(mts, monkeypatch):
    signals = [sig("ALPHA", grade="A", alignment=60), sig("BRAVO", grade="D", alignment=100),
               sig("CHARLIE", grade="B", alignment=70), sig("DELTA", grade="F", alignment=10)]
    seen = run_screen(mts, monkeypatch, signals, enhanced_by_symbol={"ALPHA": 95.0, "CHARLIE": 20.0})
    # ALPHA 95 (ML), BRAVO 60 (100*.6, no ML), CHARLIE 20 (ML), DELTA 6 (10*.6)
    assert seen["results_order"] == ["ALPHA", "BRAVO", "CHARLIE", "DELTA"]
    assert seen["ledger_order"] == seen["results_order"]


def test_without_any_ml_the_order_is_by_alignment_not_the_sql_alphabetical_order(mts, monkeypatch):
    signals = [sig("AAA", grade="C", alignment=50), sig("ZZZ", grade="A", alignment=90), sig("MMM", grade="B", alignment=70)]
    assert run_screen(mts, monkeypatch, signals)["results_order"] == ["ZZZ", "MMM", "AAA"]


def test_guard_drops_happen_before_scoring_and_before_the_ledger_write(mts, monkeypatch):
    signals = [sig("KEEP", alignment=60), sig("ILLIQUID", alignment=99)]
    seen = run_screen(mts, monkeypatch, signals, guard_survivors={"KEEP"})
    assert seen["results_order"] == ["KEEP"] and seen["ledger_order"] == ["KEEP"]


def test_the_ledger_receives_the_explicit_session_after_the_guards(mts, monkeypatch):
    assert run_screen(mts, monkeypatch, [sig("X")])["session"] == SESSION


# ------------------------------------------------------------------ one boundary gates ALL the new behaviour
# Deploying the image with no boundary (or screening a session before it) must reproduce 4d9bf93 exactly: SQL order, no
# merged combined_score fields, no ledger-integrity refusals. On/after the boundary: the new behaviour. Explicit session
# dates only -- never the clock.
LEGACY_SIGNALS = lambda: [sig("AAA", grade="C", alignment=50), sig("ZZZ", grade="A", alignment=90), sig("MMM", grade="B", alignment=70)]  # noqa: E731


def _count_merges(mts, monkeypatch):
    calls, real = [], mts.MultiTimeframeMLScreener._merge_ml_scores

    def spy(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)
    monkeypatch.setattr(mts.MultiTimeframeMLScreener, "_merge_ml_scores", spy)
    return calls


def _boundary(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("GUARDS_EFFECTIVE_FROM", raising=False)
    else:
        monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", value)


@pytest.mark.parametrize("value", [None, "", "2026-09-30"], ids=["unset", "blank", "session-before-boundary"])
def test_legacy_behaviour_when_unset_or_before_the_boundary(mts, monkeypatch, caplog, value):
    caplog.set_level("INFO")
    _boundary(monkeypatch, value)
    merges = _count_merges(mts, monkeypatch)
    seen = run_screen(mts, monkeypatch, LEGACY_SIGNALS())
    assert seen["results_order"] == ["AAA", "ZZZ", "MMM"] and seen["ledger_order"] == ["AAA", "ZZZ", "MMM"]  # SQL order kept
    assert merges == []                                                                                      # no merge / fallback fields
    assert seen["strict"] is False                                                                           # no ledger refusals
    assert "Screener behaviour mode: LEGACY" in caplog.text and "Screener behaviour mode: NEW" not in caplog.text


@pytest.mark.parametrize("session", [SESSION], ids=["boundary-session"])
def test_new_behaviour_on_the_boundary_session(mts, monkeypatch, caplog, session):
    caplog.set_level("INFO")
    _boundary(monkeypatch, SESSION.isoformat())
    s, _ = screener(mts, monkeypatch, [], target=session)
    assert s.guards_active is True
    merges = _count_merges(mts, monkeypatch)
    seen = run_screen(mts, monkeypatch, LEGACY_SIGNALS())      # run_screen builds its own screener for SESSION
    assert seen["results_order"] == ["ZZZ", "MMM", "AAA"] and len(merges) == 1
    assert seen["strict"] is True
    assert "Screener behaviour mode: NEW" in caplog.text


def test_the_boundary_session_itself_is_new_and_the_day_before_is_legacy(mts, monkeypatch):
    _boundary(monkeypatch, SESSION.isoformat())
    before, _ = screener(mts, monkeypatch, [], target=SESSION - timedelta(days=1))
    on, _ = screener(mts, monkeypatch, [], target=SESSION)
    assert (before.guards_active, on.guards_active) == (False, True)


def test_replaying_a_historical_session_is_deterministic(mts, monkeypatch):
    """Same (boundary, session) => same behaviour however many times, whenever it is run."""
    _boundary(monkeypatch, "2026-10-07")
    runs = [run_screen(mts, monkeypatch, LEGACY_SIGNALS()) for _ in range(3)]       # SESSION 2026-09-29 < boundary
    assert all(r["results_order"] == ["AAA", "ZZZ", "MMM"] and r["strict"] is False for r in runs)


def test_no_gated_behaviour_reads_the_clock(mts):
    src = inspect.getsource(mts.MultiTimeframeMLScreener.screen_all_symbols)
    assert "date.today" not in src and "MECHANISM_SHA" not in src   # (datetime.now there is only run timing)
    assert src.count("self.guards_active") >= 3          # merge, sort, ledger strictness


# ------------------------------------------------------------------ structure
def test_timing_decorator_sits_on_screen_all_symbols_not_on_the_guard(mts):
    cls = mts.MultiTimeframeMLScreener
    assert hasattr(cls.screen_all_symbols, "__wrapped__"), "screen_all_symbols lost its @timing_decorator()"
    assert not hasattr(cls._apply_universe_guards, "__wrapped__")
    assert not hasattr(cls._merge_ml_scores, "__wrapped__")


def test_the_guard_uses_the_shared_helpers_not_a_reimplementation(mts):
    src = inspect.getsource(mts.ug)
    for needle in ("pf.compute_indicators", "pf.find_discontinuities", "pf.MIN_DOLLAR_VOLUME_20",
                   "pf.LOOKBACK_BARS", "dbld.MIN_BARS"):
        assert needle in src
    assert "ug.reasons_for_rows" in inspect.getsource(mts.MultiTimeframeMLScreener._apply_universe_guards)


# ------------------------------------------------------------------ named reasons (the observer's input)
def decisions(mts, monkeypatch, rows, symbols):
    s, _ = screener(mts, monkeypatch, rows)
    s._apply_universe_guards([sig(sym) for sym in symbols])
    return s.guard_decisions


def test_each_failing_rule_has_its_own_reason_and_a_passing_symbol_has_none(mts, monkeypatch):
    jump = {i: {"open": 500.0, "high": 505.0, "low": 495.0, "close": 500.0} for i in range(70, 80)}
    rows = (bars("SHORT", 40) + bars("THIN", 80, volume=1_000.0) + bars("GOOD", 80)
            + bars("SPLIT", 80, overrides=jump))
    d = decisions(mts, monkeypatch, rows, ["SHORT", "THIN", "GOOD", "SPLIT", "GHOST"])
    assert d["SHORT"] == ["insufficient_history"]
    assert d["GHOST"] == ["insufficient_history"]
    assert d["THIN"] == ["illiquid_dollar_volume"]
    assert d["GOOD"] == []
    assert "price_discontinuity" in d["SPLIT"]


def test_a_symbol_failing_two_rules_reports_both(mts, monkeypatch):
    jump = {i: {"open": 500.0, "high": 505.0, "low": 495.0, "close": 500.0} for i in range(70, 80)}
    d = decisions(mts, monkeypatch, bars("BOTH", 80, volume=1_000.0, overrides=jump), ["BOTH"])
    assert d["BOTH"] == ["illiquid_dollar_volume", "price_discontinuity"]


def test_decisions_agree_exactly_with_the_survivor_filter(mts, monkeypatch):
    rows = bars("SHORT", 40) + bars("THIN", 80, volume=1_000.0) + bars("GOOD", 80) + bars("EDGE", 80, volume=10_000.0)
    syms = ["SHORT", "THIN", "GOOD", "EDGE"]
    s, _ = screener(mts, monkeypatch, rows)
    kept = [x["symbol"] for x in s._apply_universe_guards([sig(x) for x in syms])]
    assert kept == [sym for sym in syms if not s.guard_decisions[sym]] == ["GOOD", "EDGE"]
    assert s.guards_evaluated is True


def test_inert_guards_mean_not_evaluated_never_passed(mts, monkeypatch):
    monkeypatch.delenv("GUARDS_EFFECTIVE_FROM")
    s, db = screener(mts, monkeypatch, bars("GOOD", 80))
    out = s._apply_universe_guards([sig("GOOD")])
    assert [x["symbol"] for x in out] == ["GOOD"] and s.guards_evaluated is False and s.guard_decisions == {}
    assert db.calls == []