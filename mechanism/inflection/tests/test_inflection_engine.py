"""Fundamental Inflection Engine: features, availability, event rules, scoring, composite event, point-in-time safety and event identity. Hand-computed numbers, no database."""
import copy
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from inflection import config as C  # noqa: E402
from inflection import context as X  # noqa: E402
from inflection import events as E  # noqa: E402
from inflection import features as F  # noqa: E402
from inflection import pipeline as P  # noqa: E402

D = date.fromisoformat
ENDS = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]


def q(end, rev, oi, ni, ocf=None, fcf=None, fy=None, fq=None, upd="2026-09-18"):
    return {"quarter": D(end), "fiscal_year": fy, "fiscal_quarter": fq, "revenue": rev, "operating_income": oi, "net_income": ni, "operating_cash_flow": ocf, "free_cash_flow": fcf, "updated_at": upd}


def series(revs, ois, nis, ocfs=None, fcfs=None):
    return [q(ENDS[i], revs[i], ois[i], nis[i], None if ocfs is None else ocfs[i], None if fcfs is None else fcfs[i]) for i in range(len(revs))]


def feats(rows, sector="Technology"):
    return F.compute_features([F.quarter_from_row(r) for r in rows], sector)


def last(rows, sector="Technology"):
    return feats(rows, sector)[-1]["metrics"]


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ features
def test_growth_acceleration_and_margins_from_hand_numbers():
    # revenue: 100,100,100,100 | 110,120,140,170 -> yoy of the last quarter 70%, the quarter before 40%: acceleration 30 pp
    rev = [100, 100, 100, 100, 110, 120, 140, 170]
    oi = [10, 10, 10, 10, 12, 14, 20, 34]
    m = last(series(rev, oi, [5] * 8))
    assert m["rev_yoy"] == pytest.approx(0.70) and m["rev_yoy_prev"] == pytest.approx(0.40) and m["rev_accel_pp"] == pytest.approx(30.0)
    assert m["rev_qoq"] == pytest.approx(170 / 140 - 1)
    assert m["op_margin"] == pytest.approx(0.20) and m["op_margin_py"] == pytest.approx(0.10) and m["op_margin_yoy_bps"] == pytest.approx(1000.0)
    assert m["op_leverage"] == pytest.approx((34 / 10 - 1) / 0.70)           # operating income grew 240% on 70% revenue growth


def test_operating_leverage_is_undefined_on_a_non_positive_base_and_says_why():
    rows = series([100] * 4 + [120] * 4, [-5, -5, -5, -5, 4, 4, 4, 4], [1] * 8)
    f = feats(rows)[-1]
    assert f["metrics"]["op_leverage"] is None and "operating_leverage_undefined_non_positive_operating_income" in f["notes"]


def test_a_missing_prior_year_leaves_growth_missing_and_marks_the_quarter_partial():
    f = feats(series([100, 110, 120], [10, 11, 12], [5, 5, 5]))[-1]
    assert f["metrics"]["rev_yoy"] is None and f["quality"] == C.DATA_QUALITY_PARTIAL and "no_prior_year_quarter" in f["notes"]


def test_missing_values_are_never_filled():
    rows = series([100] * 4 + [120] * 4, [10] * 8, [5] * 8)
    rows[-1]["net_income"] = None
    f = feats(rows)[-1]
    assert f["metrics"]["net_income"] is None and f["metrics"]["ni_swing_pct_rev"] is None and "missing:net_income" in f["notes"]


def test_52_53_week_calendars_still_match_the_prior_year_quarter():
    rows = series([100] * 4 + [120] * 4, [10] * 8, [5] * 8)
    rows[4] = {**rows[4], "quarter": D("2025-03-29")}                        # a 52-week fiscal year ends a few days off
    rows[-1] = {**rows[-1], "quarter": D("2025-12-27")}
    assert feats(rows)[-1]["metrics"]["rev_yoy"] == pytest.approx(0.20)


def test_financial_and_real_estate_sectors_get_no_operating_metrics_or_fcf():
    rows = series([100] * 4 + [150] * 4, [10] * 4 + [30] * 4, [5] * 8, fcfs=[1, 1, 1, 1, 9, 9, 9, 9])
    f = feats(rows, "Financial Services")[-1]
    m = f["metrics"]
    assert m["op_margin"] is None and m["op_leverage"] is None and m["fcf_margin_yoy_pp"] is None and "non_standard_reporting_sector_no_operating_metrics" in f["notes"]
    assert [e["event_type"] for e in E.detect_base_events(m, "Financial Services")] == [] or all(e["event_type"] in (C.REVENUE_ACCELERATION, C.PROFITABILITY_TURNAROUND) for e in E.detect_base_events(m, "Financial Services"))


def test_a_suspect_revenue_jump_is_flagged_and_never_becomes_an_event():
    rows = series([100] * 4 + [100, 100, 100, 400], [10] * 4 + [10, 10, 10, 80], [5] * 4 + [5, 5, 5, 60])
    f = feats(rows)[-1]
    assert f["metrics"]["inorganic_suspect"] == 1.0 and "revenue_jump_suspect_acquisition_or_reclassification" in f["notes"]
    assert E.detect_base_events(f["metrics"]) == []


def test_features_never_look_at_later_quarters():
    rows = series([100] * 4 + [110, 120, 140, 170], [10] * 4 + [12, 14, 20, 34], [5] * 8)
    a = feats(rows[:6])
    b = feats(rows)                                                           # two more quarters appended
    for i in range(6):
        assert a[i]["metrics"] == b[i]["metrics"] and a[i]["notes"] == b[i]["notes"]


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ availability
def test_availability_uses_the_earnings_report_in_its_window_else_an_assumed_lag_else_first_seen():
    qe = D("2025-06-30")
    assert F.availability(qe, [D("2025-05-01"), D("2025-08-05"), D("2025-11-04")]) == (D("2025-08-05"), "earnings_calendar")
    assert F.availability(qe, []) == (qe + timedelta(days=75), "assumed_lag_75d")
    assert F.availability(qe, [D("2025-07-05")]) == (D("2025-07-05"), "earnings_calendar")      # the window opens 5 days after quarter end
    assert F.availability(qe, [D("2025-07-01")])[1].startswith("assumed")                      # a report within 5 days of quarter end is the previous quarter's
    assert F.availability(qe, [D("2025-07-02") + timedelta(days=200)])[1].startswith("assumed")      # a report outside the window is not this quarter's
    assert P.resolve_availability(qe, [], "2025-08-10") == (D("2025-08-10"), "first_seen")           # we hold the figures earlier than the assumed lag
    assert P.resolve_availability(qe, [], "2026-01-01") == (qe + timedelta(days=75), "assumed_lag_75d")


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ event rules
def base_metrics(**over):
    m = {"revenue": 120.0, "revenue_py": 100.0, "rev_yoy": 0.20, "rev_yoy_prev": 0.05, "rev_accel_pp": 15.0, "rev_qoq": 0.05, "inorganic_suspect": 0.0, "operating_income": 20.0, "op_margin": 0.1667,
         "op_margin_py": 0.10, "op_margin_yoy_bps": 667.0, "op_leverage": 3.5, "net_income": 10.0, "net_income_py": -4.0, "net_income_pq": -1.0, "ni_swing_pct_rev": 14 / 120,
         "free_cash_flow": 8.0, "free_cash_flow_py": -3.0, "fcf_margin": 0.0667, "fcf_margin_yoy_pp": 9.0, "cash_conversion": 1.2, "operating_cash_flow": 12.0}
    m.update(over)
    return m


def kinds(m, sector="Technology"):
    return {e["event_type"]: e for e in E.detect_base_events(m, sector)}


def test_every_event_type_fires_on_a_real_improvement_and_explains_itself_with_its_numbers():
    ev = kinds(base_metrics())
    assert set(ev) == {C.REVENUE_ACCELERATION, C.MARGIN_EXPANSION, C.OPERATING_LEVERAGE, C.PROFITABILITY_TURNAROUND, C.FCF_TURNAROUND}
    assert "+20.0%" in ev[C.REVENUE_ACCELERATION]["explanation"][0] and "15.0 percentage points" in ev[C.REVENUE_ACCELERATION]["explanation"][0]
    assert "+667 basis points" in ev[C.MARGIN_EXPANSION]["explanation"][0]
    assert "3.5 times as fast" in ev[C.OPERATING_LEVERAGE]["explanation"][0]
    assert "turned positive" in ev[C.PROFITABILITY_TURNAROUND]["explanation"][0] and "previous quarter" in ev[C.PROFITABILITY_TURNAROUND]["explanation"][1]
    assert all(0 <= e["strength"] <= 100 and e["severity"] in ("low", "medium", "high") for e in ev.values())


@pytest.mark.parametrize("override,kind", [
    ({"rev_accel_pp": 4.9}, C.REVENUE_ACCELERATION), ({"rev_yoy": 0.09}, C.REVENUE_ACCELERATION), ({"rev_qoq": -0.01}, C.REVENUE_ACCELERATION),
    ({"op_margin_yoy_bps": 299.0}, C.MARGIN_EXPANSION), ({"op_margin": 0.0}, C.MARGIN_EXPANSION),
    ({"op_leverage": 1.9}, C.OPERATING_LEVERAGE), ({"rev_yoy": 0.07}, C.OPERATING_LEVERAGE),
    ({"net_income_py": 1.0}, C.PROFITABILITY_TURNAROUND), ({"ni_swing_pct_rev": 0.019}, C.PROFITABILITY_TURNAROUND), ({"operating_income": -1.0}, C.PROFITABILITY_TURNAROUND),
    ({"free_cash_flow_py": 2.0}, C.FCF_TURNAROUND), ({"fcf_margin_yoy_pp": 2.9}, C.FCF_TURNAROUND), ({"free_cash_flow": -1.0}, C.FCF_TURNAROUND),
])
def test_just_below_a_threshold_the_event_does_not_fire(override, kind):
    assert kind not in kinds(base_metrics(**override))


def test_strength_is_50_at_the_threshold_and_100_at_the_strong_value():
    t = C.THRESHOLDS[C.MARGIN_EXPANSION]
    at = kinds(base_metrics(op_margin_yoy_bps=t["opm_yoy_bps_min"]))[C.MARGIN_EXPANSION]["strength"]
    strong = kinds(base_metrics(op_margin_yoy_bps=t["opm_yoy_bps_strong"]))[C.MARGIN_EXPANSION]["strength"]
    beyond = kinds(base_metrics(op_margin_yoy_bps=5000.0))[C.MARGIN_EXPANSION]["strength"]
    assert at == 50.0 and strong == 100.0 and beyond == 100.0


def test_poor_cash_conversion_costs_a_turnaround_strength_and_says_so():
    good = kinds(base_metrics(cash_conversion=1.2, ni_swing_pct_rev=0.05))[C.PROFITABILITY_TURNAROUND]
    poor = kinds(base_metrics(cash_conversion=0.3, ni_swing_pct_rev=0.05))[C.PROFITABILITY_TURNAROUND]
    assert poor["strength"] == pytest.approx(good["strength"] - C.CASH_CONVERSION_PENALTY) and any("Caution" in s for s in poor["explanation"])


def test_a_turnaround_that_was_also_a_loss_last_quarter_gets_the_sequential_bonus():
    seq = kinds(base_metrics(net_income_pq=-1.0, ni_swing_pct_rev=0.05))[C.PROFITABILITY_TURNAROUND]["strength"]
    one_off = kinds(base_metrics(net_income_pq=2.0, ni_swing_pct_rev=0.05))[C.PROFITABILITY_TURNAROUND]["strength"]
    assert seq - one_off == C.SEQUENTIAL_BONUS


def test_the_score_is_a_transparent_weighted_sum_and_its_components_add_up():
    ev = list(kinds(base_metrics()).values())
    sc = E.score(ev, base_metrics())
    assert abs(sum(c["contribution"] for c in sc["components"]) - sc["score"]) < 0.11 or sc["score"] == 100.0
    assert 0 <= sc["score"] <= 100 and sc["n_event_types"] == 5
    assert abs(sum(C.SCORE_WEIGHTS.values()) + C.QUALITY_BONUS_WEIGHT - 1.0) < 1e-9
    assert E.score([], base_metrics())["score"] == 0.0


def test_the_composite_event_needs_a_real_inflection_and_a_completed_window_with_little_repricing():
    ev = list(kinds(base_metrics()).values())
    sc = E.score(ev, base_metrics())
    assert E.limited_repricing(sc, base_metrics(), None, ev) is None                      # window not complete: not knowable, not emitted
    assert E.limited_repricing(sc, base_metrics(), 0.25, ev) is None                      # the market already paid for it
    got = E.limited_repricing(sc, base_metrics(), 0.01, ev)
    assert got and got["event_type"] == C.LIMITED_REPRICING and "relative to its sector" in got["explanation"][1]
    one = [ev[0]]
    assert E.limited_repricing(E.score(one, base_metrics()), base_metrics(), 0.0, one) is None   # a single event type is not enough


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ pipeline: point in time, identity
def _prices(n=400, start=D("2025-10-01"), drift=0.0):
    ds, d = [], start
    while len(ds) < n:
        if d.weekday() < 5:
            ds.append(d)
        d += timedelta(days=1)
    c = 50.0 * np.exp(np.cumsum(np.full(n, drift)))
    return X.Prices(ds, c)


def _sector_panel(drift=0.0, n=12):
    px = {f"P{i}": _prices(drift=drift) for i in range(n)}
    return px, {s: "Technology" for s in px}


ROWS = series([100] * 4 + [110, 120, 140, 170], [10] * 4 + [12, 14, 20, 34], [5] * 4 + [-1, -1, 2, 9], [6] * 8, [4] * 4 + [-2, -2, 3, 8])


def test_nothing_is_reported_before_the_quarter_was_public():
    before = P.build_symbol("T", ROWS, [D("2026-02-10")], "Technology", None, None, None, D("2026-02-09"))
    after = P.build_symbol("T", ROWS, [D("2026-02-10")], "Technology", None, None, None, D("2026-02-10"))
    assert not any(f["quarter_end"] == D("2025-12-31") for f in before[0]) and any(f["quarter_end"] == D("2025-12-31") for f in after[0])
    assert not any(e["quarter_end"] == D("2025-12-31") for e in before[1]) and any(e["quarter_end"] == D("2025-12-31") for e in after[1])


def test_events_as_of_an_earlier_date_do_not_change_when_later_data_arrives():
    px, sec = _sector_panel()
    px["T"] = _prices(drift=0.0004)
    sec["T"] = "Technology"
    idx = X.SectorIndex(px, sec)
    asof = D("2026-02-20")
    f1, e1 = P.build_symbol("T", ROWS, [D("2026-02-10")], "Technology", px["T"], idx, None, asof)
    # the future: more quarters and a different price history after `asof`
    rows2 = ROWS + [q("2026-03-31", 400, 120, 60)]
    later = X.Prices(px["T"].dates, px["T"].close.copy())
    later.close[px["T"].dates.index(D("2026-02-23")):] *= 3.0
    idx2 = X.SectorIndex({**px, "T": later}, sec)
    f2, e2 = P.build_symbol("T", rows2, [D("2026-02-10"), D("2026-05-12")], "Technology", later, idx2, None, asof)
    strip = lambda recs: [{k: v for k, v in r.items() if k not in ("context",)} for r in recs]     # noqa: E731
    assert strip(f1) == strip(f2) and strip(e1) == strip(e2)


def test_the_composite_event_is_dated_by_the_end_of_its_window_and_absent_until_then():
    px, sec = _sector_panel()
    px["T"] = _prices(drift=0.0)
    sec["T"] = "Technology"
    idx = X.SectorIndex(px, sec)
    avail = D("2026-02-10")
    win = X.repricing_window(px["T"], avail, C.LIMITED_REPRICING_PARAMS["reprice_sessions"])
    early = P.build_symbol("T", ROWS, [avail], "Technology", px["T"], idx, None, win[1] - timedelta(days=1))[1]
    on_time = P.build_symbol("T", ROWS, [avail], "Technology", px["T"], idx, None, win[1])[1]
    last_q = D("2025-12-31")
    assert not [e for e in early if e["event_type"] == C.LIMITED_REPRICING and e["quarter_end"] == last_q]
    comp = [e for e in on_time if e["event_type"] == C.LIMITED_REPRICING and e["quarter_end"] == last_q]
    assert comp and comp[0]["available_at"] == win[1] and comp[0]["availability_basis"].startswith("repricing_window_end")


def test_event_identity_ignores_market_context_but_changes_with_the_restated_numbers():
    _, ev = P.build_symbol("T", ROWS, [D("2026-02-10")], "Technology", None, None, None, D("2026-03-01"))
    e = next(e for e in ev if e["event_type"] == C.MARGIN_EXPANSION and e["quarter_end"] == D("2025-12-31"))
    same = {**e, "context": {"anything": 1}}
    assert P.event_hash(same) == e["event_hash"]
    restated = copy.deepcopy(ROWS)
    restated[-1]["operating_income"] = 38                                         # the quarter was restated
    _, ev2 = P.build_symbol("T", restated, [D("2026-02-10")], "Technology", None, None, None, D("2026-03-01"))
    e2 = next(x for x in ev2 if x["event_type"] == C.MARGIN_EXPANSION and x["quarter_end"] == D("2025-12-31"))
    assert e2["event_hash"] != e["event_hash"]


def test_every_record_carries_period_source_availability_versions_and_quality():
    f, ev = P.build_symbol("T", ROWS, [D("2026-02-10")], "Technology", None, None, None, D("2026-03-01"))
    for r in f + ev:
        for k in ("symbol", "quarter_end", "fiscal_year", "fiscal_quarter", "source", "source_updated_at", "available_at", "availability_basis", "method_version", "data_quality", "quality_notes"):
            assert k in r
        assert r["method_version"] == C.METHOD_VERSION and r["source"] == C.SOURCE_QUARTERLY
    for e in ev:
        assert e["components"] and e["explanation"] and e["event_hash"] and "metrics" in e


def test_the_sector_benchmark_needs_enough_members_and_excess_return_is_stock_minus_sector():
    px, sec = _sector_panel(drift=0.001, n=10)
    px["T"] = _prices(drift=0.003)
    sec["T"] = "Technology"
    idx = X.SectorIndex(px, sec)
    d0, d1 = px["T"].dates[50], px["T"].dates[70]
    ex = X.excess_return(px["T"], idx, "Technology", d0, d1)
    stock = px["T"].close[70] / px["T"].close[50] - 1
    assert ex is not None and ex == pytest.approx(stock - idx.ret("Technology", d0, d1)) and ex > 0
    tiny = X.SectorIndex({"A": px["T"]}, {"A": "Solo"})
    assert X.excess_return(px["T"], tiny, "Solo", d0, d1) is None


def test_valuation_context_uses_snapshots_on_or_before_the_date_only():
    rows = [{"symbol": "T", "date": D("2025-12-01") + timedelta(days=7 * i), "ps_ratio": 2.0 + i * 0.1, "pe_ratio": 20.0, "market_cap": 1e9, "sector": "Technology"} for i in range(20)]
    store = X.ValuationStore(rows)
    c = store.context("T", D("2026-01-15"), "Technology")
    assert c["snapshot_date"] <= "2026-01-15" and c["ps_percentile_own_history"] is None or c["ps_percentile_own_history"] == pytest.approx(1.0)
    assert store.context("T", D("2025-01-01"), "Technology")["snapshot_date"] is None
    assert store.context("ZZZ", D("2026-01-15"), "Technology")["ps_ratio"] is None
