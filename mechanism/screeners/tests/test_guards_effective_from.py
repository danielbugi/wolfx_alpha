"""GUARDS_EFFECTIVE_FROM: the explicit, session-dated switch for the screener's universe guards.

Contract under test: unset/blank => inert (deploying an image changes nothing); YYYY-MM-DD => guards apply to sessions
on/after that date only; the decision is a pure function of (boundary, session) -- never the wall clock, never the image
pin time -- and a malformed value raises instead of being read as "unset". No network, no database.

Run:  python -m pytest mechanism/screeners/tests/test_guards_effective_from.py -q      (from the repo root)
"""
import inspect
import os
import re
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from screeners import guards_boundary as gb  # noqa: E402

D = date(2026, 10, 7)


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("raw", [None, "", "   ", "\t"])
def test_unset_or_blank_is_inert(raw):
    assert gb.parse_effective_from(raw) is None
    assert gb.guards_apply(D, gb.parse_effective_from(raw)) is False


def test_valid_iso_date_parses_and_surrounding_whitespace_is_tolerated():
    assert gb.parse_effective_from("2026-10-07") == D
    assert gb.parse_effective_from(" 2026-10-07\n") == D


@pytest.mark.parametrize("raw", ["2026-13-01", "2026-02-30", "2026-1-7", "20261007", "10/07/2026", "2026-10-07T00:00:00",
                                 "today", "now", "yesterday", "1", "true", "2026-10-07 2026-10-08", "0000-00-00"])
def test_a_malformed_value_raises_it_is_never_read_as_unset(raw):
    with pytest.raises(gb.GuardsBoundaryError):
        gb.parse_effective_from(raw)


def test_effective_from_reads_the_environment_mapping():
    assert gb.effective_from({}) is None
    assert gb.effective_from({"GUARDS_EFFECTIVE_FROM": "2026-10-07"}) == D
    with pytest.raises(gb.GuardsBoundaryError):
        gb.effective_from({"GUARDS_EFFECTIVE_FROM": "soon"})


# ------------------------------------------------------------------ the boundary semantics
def test_sessions_before_the_boundary_are_inert_on_and_after_are_guarded():
    assert gb.guards_apply(D - timedelta(days=1), D) is False
    assert gb.guards_apply(D, D) is True            # the boundary session itself is the first guarded session
    assert gb.guards_apply(D + timedelta(days=1), D) is True
    assert gb.guards_apply(D + timedelta(days=365), D) is True


def test_decision_is_reproducible_for_a_historical_session():
    """Re-screening an old session gives the same answer however much later it runs."""
    answers = {gb.guards_apply(date(2026, 10, 2), D) for _ in range(5)}
    assert answers == {False}


def test_a_far_future_boundary_keeps_guards_inert():
    assert gb.guards_apply(D, date(2099, 1, 1)) is False


def test_boundary_module_never_reads_the_clock_or_the_image_pin():
    code = re.sub(r'""".*?"""', "", inspect.getsource(gb), flags=re.S)
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
    for needle in (".today(", ".now(", "time.time", "CURRENT_MECHANISM_SHA", "mechanism_image_tag", "pandas", "ml_training"):
        assert needle not in code, needle


def test_describe_states_mode_session_and_boundary():
    assert gb.describe(D, None) == "Universe guards mode: INERT (session 2026-10-07; GUARDS_EFFECTIVE_FROM is unset)"
    assert gb.describe(D - timedelta(days=1), D) == (
        "Universe guards mode: INERT (session 2026-10-06 < GUARDS_EFFECTIVE_FROM=2026-10-07)")
    assert gb.describe(D, D) == "Universe guards mode: ACTIVE (session 2026-10-07 >= GUARDS_EFFECTIVE_FROM=2026-10-07)"


# ------------------------------------------------------------------ wired into the screener
@pytest.fixture
def mts(monkeypatch):
    try:
        from screeners import multi_timeframe_screener as m
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"screener module not importable here: {type(e).__name__}: {e}")
    monkeypatch.setattr(m, "ML_AVAILABLE", False)
    monkeypatch.delenv("GUARDS_EFFECTIVE_FROM", raising=False)

    class RecordingDB:
        def __init__(self):
            self.calls = []

        def execute_dict_query(self, *a, **k):
            self.calls.append((a, k))
            return []

    monkeypatch.setattr(m, "db", RecordingDB())
    return m


def _sig(sym):
    return {"symbol": sym, "signal_type": "bullish_breakout", "screening_date": D, "alignment_grade": "A",
            "alignment_score": 80}


def test_screener_is_inert_when_the_variable_is_unset(mts, caplog):
    caplog.set_level("INFO")
    s = mts.MultiTimeframeMLScreener(target_session=D)
    signals = [_sig("AAA")]
    assert s.guards_active is False and s.guards_boundary is None
    assert s._apply_universe_guards(signals) is signals
    assert mts.db.calls == [] and s.guards_evaluated is False and s.guard_decisions == {}
    assert "Universe guards mode: INERT" in caplog.text and "dropped" not in caplog.text


def test_screener_is_inert_for_a_session_before_the_boundary(mts, monkeypatch):
    monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", "2026-10-07")
    s = mts.MultiTimeframeMLScreener(target_session=date(2026, 10, 6))
    signals = [_sig("AAA")]
    assert s.guards_active is False
    assert s._apply_universe_guards(signals) is signals and mts.db.calls == []


@pytest.mark.parametrize("session", [date(2026, 10, 7), date(2026, 10, 8)])
def test_screener_evaluates_guards_on_and_after_the_boundary(mts, monkeypatch, caplog, session):
    if not mts.GUARDS_AVAILABLE:
        pytest.skip("universe guards not importable here")
    caplog.set_level("INFO")
    monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", "2026-10-07")
    s = mts.MultiTimeframeMLScreener(target_session=session)
    out = s._apply_universe_guards([_sig("AAA")])
    assert s.guards_active is True and s.guards_evaluated is True
    assert out == []                         # no price rows for AAA at all => insufficient history => dropped
    assert mts.db.calls                      # and the guard really ran its query
    assert "Universe guards mode: ACTIVE" in caplog.text and "Universe guards: dropped 1/1 symbols" in caplog.text


def test_an_invalid_value_fails_construction_not_silently_inert(mts, monkeypatch):
    monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", "2026-10-7")
    with pytest.raises(gb.GuardsBoundaryError):
        mts.MultiTimeframeMLScreener(target_session=D)


def test_the_screener_decision_has_no_wall_clock_or_pin_fallback(mts):
    src = inspect.getsource(mts.MultiTimeframeMLScreener.__init__) + inspect.getsource(
        mts.MultiTimeframeMLScreener._apply_universe_guards)
    assert "gb.guards_apply(target_session, self.guards_boundary)" in src
    assert "date.today" not in src and "MECHANISM_SHA" not in src


def test_results_metadata_records_whether_guards_applied(mts, monkeypatch):
    monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", "2026-10-07")
    for session, applied in ((date(2026, 10, 6), False), (D, True)):
        s = mts.MultiTimeframeMLScreener(target_session=session)
        assert s._guards_metadata() == {"applied": applied, "effective_from": "2026-10-07",
                                        "session": session.isoformat()}


def test_the_failed_run_path_when_guards_are_required_but_unavailable(mts, monkeypatch):
    monkeypatch.setenv("GUARDS_EFFECTIVE_FROM", "2000-01-01")
    monkeypatch.setattr(mts, "GUARDS_AVAILABLE", False)
    s = mts.MultiTimeframeMLScreener(target_session=D)
    monkeypatch.setattr(s, "get_multi_timeframe_signals", lambda: [_sig("AAA")])
    s.failed = False
    s.screen_all_symbols()
    assert s.failed is True
