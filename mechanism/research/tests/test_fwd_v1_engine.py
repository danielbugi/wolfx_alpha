"""fwd_v1 pure engine: maturity, anti-leakage, long/short, MFE/MAE, missing data, benchmark, discontinuities,
determinism. No database. The label table's own constraints are in test_fwd_v1_db.py."""
import math
import os
import re
from datetime import date, timedelta

import pytest

from conftest import ROOT  # noqa: F401  (puts mechanism/ on sys.path)
from research.labels import fwd_v1 as f

T0 = date(2099, 1, 5)


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


CAL = weekdays(T0, 90)           # CAL[0] == T0 is a weekday-start in this fixture (asserted below)
assert CAL[0] == T0 and T0.weekday() < 5


def series(closes, start=0, spread=0.01):
    """Bars for CAL[start:] from a list of closes; high/low = close * (1 +/- spread)."""
    return {CAL[start + i]: f.Bar(CAL[start + i], c, c, c * (1 + spread), c * (1 - spread)) for i, c in enumerate(closes)}


def bench_series(closes, start=0):
    return {CAL[start + i]: c for i, c in enumerate(closes)}


def obs(direction=1, entry=100.0, t0=T0, oid=1):
    return f.Observation(oid, "AAA", direction, t0, entry)


def flat(n, price=100.0):
    return [price] * n


def asof(i):
    return CAL[i]


# ------------------------------------------------------------------------------------------ maturity / leakage
@pytest.mark.parametrize("h", f.HORIZONS)
def test_horizon_is_the_nth_session_after_t0_and_matures_exactly_there(h):
    bars = series(flat(h + 10))
    bench = bench_series(flat(h + 10))
    before = f.compute(obs(), h, CAL, bars, bench, asof(h - 1))
    assert isinstance(before, f.Pending) and before.reason == "horizon_not_reached"
    at = f.compute(obs(), h, CAL, bars, bench, asof(h))
    assert isinstance(at, f.Label) and at.label_status == "final"
    assert at.horizon_session == CAL[h]            # sessions, not calendar days: weekends are skipped


def test_horizon_counts_sessions_not_calendar_days_across_a_weekend_and_holiday():
    cal = list(CAL)
    holiday = cal[3]
    cal.remove(holiday)                             # a market holiday: not a session
    bars = {d: f.Bar(d, 100.0, 100.0, 101.0, 99.0) for d in cal}
    bench = {d: 100.0 for d in cal}
    lab = f.compute(obs(), 5, cal, bars, bench, cal[5])
    assert lab.horizon_session == cal[5] and (lab.horizon_session - T0).days >= 7


def test_bars_after_the_horizon_cannot_change_the_label_or_its_hash():
    clean = series(flat(8) + [100.0] * 20)
    poisoned = series(flat(8) + [1.0, 1e9, 3.0] + [100.0] * 17)    # garbage strictly after horizon CAL[5]
    a = f.compute(obs(), 5, CAL, clean, bench_series(flat(30)), asof(10))
    b = f.compute(obs(), 5, CAL, poisoned, bench_series(flat(8) + [5.0] * 22), asof(10))
    assert a.input_hash == b.input_hash
    assert (a.raw_return, a.mfe, a.mae, a.benchmark_return) == (b.raw_return, b.mfe, b.mae, b.benchmark_return)


def test_a_later_as_of_does_not_change_the_outcome_only_the_audit_field():
    bars, bench = series([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]), bench_series(flat(11))
    a = f.compute(obs(), 5, CAL, bars, bench, asof(5))
    b = f.compute(obs(), 5, CAL, bars, bench, asof(10))
    assert a.computed_as_of_session != b.computed_as_of_session
    assert (a.raw_return, a.mfe, a.mae, a.input_hash) == (b.raw_return, b.mfe, b.mae, b.input_hash)


def test_a_partial_horizon_never_yields_a_label_of_any_status():
    bars = series([100, 101, 102])                  # the data ends before the horizon
    for i in range(0, 5):
        assert isinstance(f.compute(obs(), 5, CAL, bars, bench_series(flat(3)), asof(i)), f.Pending)


def test_calendar_that_does_not_reach_the_horizon_is_pending():
    short = CAL[:4]
    r = f.compute(obs(), 5, short, series(flat(4)), bench_series(flat(4)), short[-1])
    assert isinstance(r, f.Pending) and r.reason == "horizon_not_reached"


# ------------------------------------------------------------------------------------------ returns and direction
def test_raw_directional_and_benchmark_relative_returns_long():
    bars = series([100, 102, 104, 106, 108, 110])
    bench = bench_series([4000, 4010, 4020, 4030, 4040, 4080])           # +2.0%
    lab = f.compute(obs(1), 5, CAL, bars, bench, asof(5))
    assert lab.raw_return == pytest.approx(0.10)
    assert lab.directional_return == pytest.approx(0.10)
    assert lab.benchmark_state == "ok" and lab.benchmark_return == pytest.approx(0.02)
    assert lab.excess_return == pytest.approx(0.08)
    assert lab.directional_excess_return == pytest.approx(0.08)


def test_short_profits_when_price_falls_and_excess_is_direction_adjusted():
    bars = series([100, 99, 98, 97, 96, 90])
    bench = bench_series([4000, 4000, 4000, 4000, 4000, 4040])            # +1.0%
    lab = f.compute(obs(-1), 5, CAL, bars, bench, asof(5))
    assert lab.raw_return == pytest.approx(-0.10)                         # raw is the stock's own move
    assert lab.directional_return == pytest.approx(+0.10)                 # a short gained
    assert lab.excess_return == pytest.approx(-0.11)                      # raw - benchmark
    assert lab.directional_excess_return == pytest.approx(+0.11)


def test_short_loses_when_price_rises():
    lab = f.compute(obs(-1), 1, CAL, series([100, 105]), bench_series([1, 1]), asof(1))
    assert lab.directional_return == pytest.approx(-0.05) and lab.raw_return == pytest.approx(0.05)


def test_returns_are_computed_on_the_stored_basis_not_the_captured_entry_close():
    bars = series([50, 55, 55, 55, 55, 55])         # history was halved by a restatement before the label was computed
    lab = f.compute(obs(1, entry=100.0), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.reference_close == 50 and lab.raw_return == pytest.approx(0.10)   # NOT 55/100-1
    assert lab.basis_ratio == pytest.approx(2.0)
    assert lab.data_quality == "basis_adjusted" and "basis_note" in lab.dq_details


def test_matching_entry_close_is_data_quality_ok():
    lab = f.compute(obs(1, entry=100.0), 1, CAL, series([100, 101]), bench_series([1, 1]), asof(1))
    assert lab.data_quality == "ok" and lab.basis_ratio == pytest.approx(1.0)


# ------------------------------------------------------------------------------------------ MFE / MAE
def test_mfe_mae_long_use_intrabar_extremes_over_t0_plus_1_to_horizon():
    bars = series([100, 100, 100, 100, 100, 100], spread=0.0)
    bars[CAL[2]] = f.Bar(CAL[2], 100, 100, 108, 100)      # best high 108
    bars[CAL[4]] = f.Bar(CAL[4], 100, 100, 100, 95)       # worst low 95
    lab = f.compute(obs(1), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.mfe == pytest.approx(0.08) and lab.mae == pytest.approx(-0.05) and lab.path_state == "complete"


def test_mfe_mae_short_are_direction_adjusted():
    bars = series(flat(6), spread=0.0)
    bars[CAL[2]] = f.Bar(CAL[2], 100, 100, 108, 100)
    bars[CAL[4]] = f.Bar(CAL[4], 100, 100, 100, 95)
    lab = f.compute(obs(-1), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.mfe == pytest.approx(0.05) and lab.mae == pytest.approx(-0.08)


def test_the_t0_bar_itself_is_excluded_from_the_excursion_window():
    bars = series(flat(6), spread=0.0)
    bars[CAL[0]] = f.Bar(CAL[0], 100, 100, 200, 1)        # a huge T0 range must not leak into MFE/MAE
    lab = f.compute(obs(1), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.mfe == 0.0 and lab.mae == 0.0


def test_excursion_signs_hold_when_price_only_moves_one_way():
    up = f.compute(obs(1), 3, CAL, series([100, 101, 102, 103], spread=0.0), bench_series(flat(4)), asof(3))
    assert up.mfe == pytest.approx(0.03) and up.mae == 0.0 and up.mfe >= 0 >= up.mae
    dn = f.compute(obs(1), 3, CAL, series([100, 99, 98, 97], spread=0.0), bench_series(flat(4)), asof(3))
    assert dn.mfe == 0.0 and dn.mae == pytest.approx(-0.03)


def test_close_beyond_the_reported_high_low_still_counts():
    bars = series([100, 100, 100, 100], spread=0.0)
    bars[CAL[1]] = f.Bar(CAL[1], 110, 100, 105, 99)       # close 110 > high 105: treated as max(high, close)
    lab = f.compute(obs(1), 3, CAL, bars, bench_series(flat(4)), asof(3))
    assert lab.mfe == pytest.approx(0.10)


# ------------------------------------------------------------------------------------------ missing data
def test_missing_path_bar_gives_a_final_label_with_an_incomplete_path_and_null_excursions():
    bars = series(flat(6))
    del bars[CAL[2]]
    lab = f.compute(obs(), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.label_status == "final" and lab.path_state == "incomplete"
    assert lab.mfe is None and lab.mae is None and lab.bars_observed == 4
    assert lab.dq_details["missing_path_sessions"] == [CAL[2].isoformat()]


def test_bars_without_high_low_cannot_produce_excursions():
    bars = {d: f.Bar(d, 100.0) for d in CAL[:6]}
    lab = f.compute(obs(), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.label_status == "final" and lab.path_state == "incomplete" and lab.mfe is None


def test_missing_horizon_bar_waits_through_the_grace_period_then_voids():
    bars = series(flat(20))
    del bars[CAL[5]]
    for i in (5, 6, 7):                                    # horizon .. horizon+2: still repairable
        r = f.compute(obs(), 5, CAL, bars, bench_series(flat(20)), asof(i))
        assert isinstance(r, f.Pending) and r.reason == "awaiting_inputs:" + f.REASON_MISSING_HORIZON
    v = f.compute(obs(), 5, CAL, bars, bench_series(flat(20)), asof(5 + f.VOID_GRACE_SESSIONS))
    assert v.label_status == "void" and v.void_reason == f.REASON_MISSING_HORIZON
    assert v.raw_return is None and v.directional_return is None and v.mfe is None and v.horizon_close is None
    assert v.benchmark_state == "not_evaluated" and v.data_quality == "not_evaluated"


def test_missing_reference_bar_voids_after_grace():
    bars = series(flat(20))
    del bars[CAL[0]]
    r = f.compute(obs(), 5, CAL, bars, bench_series(flat(20)), asof(5))
    assert isinstance(r, f.Pending)
    v = f.compute(obs(), 5, CAL, bars, bench_series(flat(20)), asof(8))
    assert v.label_status == "void" and v.void_reason == f.REASON_MISSING_REFERENCE


def test_delisted_symbol_whose_bars_stop_before_the_horizon_voids_never_invents_a_return():
    bars = series(flat(3))                                 # trading stopped after CAL[2]
    v = f.compute(obs(), 10, CAL, bars, bench_series(flat(40)), asof(10 + f.VOID_GRACE_SESSIONS))
    assert v.label_status == "void" and v.void_reason == f.REASON_MISSING_HORIZON and v.raw_return is None


def test_halted_session_inside_the_path_is_a_gap_not_an_error():
    bars = series(flat(12))
    for i in (3, 4, 5):                                    # a three-day halt
        del bars[CAL[i]]
    lab = f.compute(obs(), 10, CAL, bars, bench_series(flat(12)), asof(10))
    assert lab.label_status == "final" and lab.path_state == "incomplete" and lab.bars_observed == 7


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf"), None])
def test_an_unusable_close_inside_the_window_is_not_used(bad):
    bars = series(flat(10))
    bars[CAL[3]] = f.Bar(CAL[3], bad, 100, 101, 99)
    r = f.compute(obs(), 5, CAL, bars, bench_series(flat(10)), asof(5))
    assert isinstance(r, f.Pending) and r.reason == "awaiting_inputs:" + f.REASON_INVALID_BAR
    v = f.compute(obs(), 5, CAL, bars, bench_series(flat(10)), asof(8))
    assert v.label_status == "void" and v.void_reason == f.REASON_INVALID_BAR


def test_high_below_low_is_an_invalid_bar():
    bars = series(flat(10))
    bars[CAL[2]] = f.Bar(CAL[2], 100, 100, 90, 110)
    assert isinstance(f.compute(obs(), 5, CAL, bars, bench_series(flat(10)), asof(5)), f.Pending)


# ------------------------------------------------------------------------------------------ benchmark
def test_benchmark_gap_waits_for_the_feed_then_finalises_with_unavailable_not_zero():
    bars, bench = series(flat(20)), bench_series(flat(20))
    del bench[CAL[5]]
    p = f.compute(obs(), 5, CAL, bars, bench, asof(5))
    assert isinstance(p, f.Pending) and p.reason == "awaiting_benchmark"
    lab = f.compute(obs(), 5, CAL, bars, bench, asof(8))
    assert lab.label_status == "final" and lab.benchmark_state == "unavailable"
    assert lab.benchmark_return is None and lab.excess_return is None and lab.directional_excess_return is None
    assert lab.raw_return == 0.0 and "benchmark_missing" in lab.dq_details     # the stock's own return is still valid


def test_no_benchmark_series_at_all_is_unavailable_after_grace():
    lab = f.compute(obs(), 1, CAL, series(flat(10)), None, asof(4))
    assert lab.label_status == "final" and lab.benchmark_state == "unavailable"


def test_a_benchmark_gap_in_the_middle_does_not_matter_only_the_two_endpoints_do():
    bench = bench_series(flat(10))
    del bench[CAL[2]]
    lab = f.compute(obs(), 5, CAL, series(flat(10)), bench, asof(5))
    assert lab.benchmark_state == "ok"


def test_zero_or_negative_benchmark_is_not_usable():
    bench = bench_series(flat(10))
    bench[CAL[5]] = 0.0
    assert isinstance(f.compute(obs(), 5, CAL, series(flat(10)), bench, asof(5)), f.Pending)


# ------------------------------------------------------------------------------------------ split / discontinuity
def test_a_split_like_jump_inside_the_window_blocks_the_label_then_voids():
    bars = series([100, 100, 100, 50, 50, 50, 50, 50])      # 2-for-1 adjustment applied midway
    p = f.compute(obs(), 5, CAL, bars, bench_series(flat(8)), asof(5))
    assert isinstance(p, f.Pending) and p.reason == "awaiting_inputs:" + f.REASON_DISCONTINUITY
    v = f.compute(obs(), 5, CAL, bars, bench_series(flat(8)), asof(8))
    assert v.label_status == "void" and v.void_reason == f.REASON_DISCONTINUITY and v.raw_return is None
    assert v.dq_details["jumps"][0]["to"] == CAL[3].isoformat()


def test_a_large_but_real_move_is_not_treated_as_a_split():
    bars = series([100, 100, 100, 80, 80, 78])               # -20%: real
    lab = f.compute(obs(), 5, CAL, bars, bench_series(flat(6)), asof(5))
    assert lab.label_status == "final" and lab.raw_return == pytest.approx(-0.22)


def test_a_reverse_split_jump_is_also_caught():
    bars = series([10, 10, 10, 20, 20, 20])
    assert isinstance(f.compute(obs(), 5, CAL, bars, bench_series(flat(6)), asof(5)), f.Pending)


def test_a_jump_before_t0_or_after_the_horizon_is_outside_the_window_and_ignored():
    bars = series([100] * 6)
    bars[CAL[6]] = f.Bar(CAL[6], 40.0, 40, 41, 39)          # strictly after the horizon
    lab = f.compute(obs(), 5, CAL, bars, bench_series(flat(8)), asof(7))
    assert lab.label_status == "final" and lab.raw_return == 0.0


# ------------------------------------------------------------------------------------------ calendar guards
def test_calendar_validation_fails_closed():
    with pytest.raises(f.CalendarError):
        f.validate_calendar([])
    with pytest.raises(f.CalendarError):
        f.validate_calendar([date(2099, 1, 3), date(2099, 1, 5)])                  # Saturday
    with pytest.raises(f.CalendarError):
        f.validate_calendar([date(2099, 1, 6), date(2099, 1, 5)])                  # not increasing
    with pytest.raises(f.CalendarError):
        f.validate_calendar([date(2099, 1, 5), date(2099, 1, 5)])                  # duplicate
    with pytest.raises(f.CalendarError):
        f.validate_calendar([date(2099, 1, 5), date(2099, 1, 12)])                 # a week missing
    assert f.validate_calendar([date(2099, 1, 2), date(2099, 1, 6)])               # Fri -> Tue is a normal long weekend


def test_as_of_must_be_a_session_of_the_calendar():
    with pytest.raises(f.CalendarError):
        f.compute(obs(), 1, CAL, series(flat(5)), bench_series(flat(5)), date(2099, 1, 10))   # a Saturday


def test_t0_outside_the_calendar_is_pending_not_guessed():
    r = f.compute(obs(t0=date(2098, 12, 1)), 1, CAL, series(flat(5)), bench_series(flat(5)), asof(3))
    assert isinstance(r, f.Pending) and r.reason == "t0_not_in_calendar"


def test_cross_check_detects_a_calendar_that_disagrees_with_the_authoritative_one():
    f.cross_check_calendar(CAL[:10], {d: 1 for d in CAL[2:6]})
    with pytest.raises(f.CalendarError):
        f.cross_check_calendar([d for d in CAL[:10] if d != CAL[3]], {d: 1 for d in CAL[2:6]})
    f.cross_check_calendar(CAL, {})                                                 # nothing to compare: no opinion


def test_invalid_horizon_or_direction_is_a_programming_error():
    with pytest.raises(ValueError):
        f.compute(obs(), 7, CAL, series(flat(10)), None, asof(9))
    with pytest.raises(ValueError):
        f.compute(obs(0), 1, CAL, series(flat(10)), None, asof(9))


# ------------------------------------------------------------------------------------------ determinism / hashing
def test_same_inputs_same_label_and_hash_regardless_of_dict_order_and_clock():
    bars = series([100, 101, 102, 103, 104, 105])
    shuffled = dict(reversed(list(bars.items())))
    a = f.compute(obs(), 5, CAL, bars, bench_series(flat(6)), asof(5))
    b = f.compute(obs(), 5, CAL, shuffled, bench_series(flat(6)), asof(5))
    assert a == b


def test_input_hash_changes_when_any_in_window_input_changes():
    base = series([100, 101, 102, 103, 104, 105])
    h0 = f.compute(obs(), 5, CAL, base, bench_series(flat(6)), asof(5)).input_hash
    for i in range(6):
        changed = dict(base)
        b = changed[CAL[i]]
        changed[CAL[i]] = f.Bar(b.session, b.close * 1.0001, b.open, b.high, b.low)
        assert f.compute(obs(), 5, CAL, changed, bench_series(flat(6)), asof(5)).input_hash != h0, i
    assert f.compute(obs(entry=100.5), 5, CAL, base, bench_series(flat(6)), asof(5)).input_hash != h0
    bench2 = bench_series(flat(6))
    bench2[CAL[5]] = 101.0
    assert f.compute(obs(), 5, CAL, base, bench2, asof(5)).input_hash != h0


def test_engine_has_no_clock_environment_or_io_dependency():
    path = os.path.join(ROOT, "mechanism", "research", "labels", "fwd_v1.py")
    text = open(path, encoding="utf-8").read()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("datetime.now", "date.today", "time.time", "os.environ", "import os", "import time", "psycopg2",
                      "open(", "requests", "random"):
        assert forbidden not in code, forbidden


def test_label_fields_are_all_finite_numbers():
    lab = f.compute(obs(), 20, CAL, series([100 + i * 0.3 for i in range(30)]), bench_series([4000 + i for i in range(30)]), asof(20))
    for name in ("raw_return", "directional_return", "benchmark_return", "excess_return", "directional_excess_return", "mfe", "mae"):
        assert math.isfinite(getattr(lab, name)), name
    assert lab.bars_observed == 20 == lab.bars_expected
