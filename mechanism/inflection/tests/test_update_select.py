"""Fundamental Updates night selection (update_select): every exclusion rule, the backlog guard, one post per company, the cap of 3, the cap of 2
per lead type (empty slots allowed), operational support, the non-operating (one-off) hold, the magnitude cap, the ranking order, the cooldown,
independence from prices and scores, and determinism. Pure: no database, no network."""
import os
import random
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from inflection import update_select as S  # noqa: E402

SESSION = date(2026, 11, 5)
METRICS = {
    "profitability_turnaround": {"net_income": 45.7e6, "net_income_py": -14.7e6, "net_income_pq": 75.2e6, "ni_swing_pct_rev": 0.138},
    "margin_expansion": {"rev_yoy": 0.055, "op_margin": 0.1524, "op_margin_py": -0.0332, "op_margin_yoy_bps": 1856.6},
    "revenue_acceleration": {"revenue": 114e6, "revenue_py": 56e6, "rev_yoy": 1.04, "rev_yoy_prev": 0.70, "rev_accel_pp": 33.8},
    "operating_leverage_inflection": {"rev_yoy": 0.21, "op_leverage": 5.6, "operating_income": 377e6},
    "fcf_turnaround": {"free_cash_flow": 7.3e6, "free_cash_flow_py": -22.7e6, "fcf_margin": 0.041, "fcf_margin_yoy_pp": 17.3},
    "improvement_with_limited_repricing": {"inflection_score": 72.5},
}


def ev(symbol="AAA", kind="margin_expansion", cap=2e9, **over):
    e = {"id": abs(hash((symbol, kind))) % 10**6, "symbol": symbol, "event_type": kind, "fiscal_year": 2026, "fiscal_quarter": 3, "quarter_end": "2026-09-30",
         "available_at": "2026-11-05", "availability_basis": "sec_acceptance", "first_detected_on": "2026-11-06", "metrics": dict(METRICS[kind]),
         "context": {"valuation_at_publication": {"market_cap": cap, "snapshot_date": "2026-11-05"}}, "company_name": f"{symbol} Corp", "cik": 1,
         "feature_notes": ["pit_as_of:2026-11-05", "prov:q=0000000001-26-000001", "prov:py=0000000001-25-000001"], "profile_date": "2026-11-05", "sector": "Technology",
         "industry": "Software"}
    e.update(over)
    return e


@pytest.mark.parametrize("over,reason", [
    ({"kind": "improvement_with_limited_repricing"}, "not_public_type"),
    ({"availability_basis": "earnings_calendar"}, "date_not_sec_verified"),
    ({"feature_notes": ["pit_as_of:2026-11-05"]}, "no_filing_accession"),
    ({"feature_notes": ["prov:q=12345"]}, "no_filing_accession"),
    ({"quarter_end": "2025-06-30", "available_at": "2026-11-05"}, "public_too_long_after_quarter_end"),
    ({"first_detected_on": "2026-11-04"}, "detected_before_public_date"),
    ({"available_at": "2026-11-01", "first_detected_on": "2026-11-06"}, "backlog_not_fresh"),
    ({"available_at": "2026-11-06", "first_detected_on": "2026-11-06"}, "public_after_session"),
    ({"cap": 2.9e8}, "below_size_floor"),
    ({"context": {}}, "below_size_floor"),
])
def test_each_exclusion_rule(over, reason):
    kind = over.pop("kind", "margin_expansion")
    cap = over.pop("cap", 2e9)
    r = S.select([ev(kind=kind, cap=cap, **over)], SESSION, {})
    assert r["selected"] == [] and [x[1] for x in r["excluded"]] == [reason]


def test_backlog_flood_is_contained():
    """A first run writing two years of history: only events public within the fresh window can be posted, and never more than 3."""
    history = [ev(f"H{i:03d}", available_at=f"2026-{m:02d}-15", quarter_end=f"2026-{m - 1:02d}-28") for i, m in enumerate([3, 4, 5, 6, 7, 8] * 50)]
    fresh = [ev(f"F{i}", kind, cap=1e9 * (i + 1)) for i, kind in enumerate(["margin_expansion", "revenue_acceleration", "fcf_turnaround"] * 2)]
    r = S.select(history + fresh, SESSION, {})
    assert len(r["selected"]) == 3 and all(c.symbol.startswith("F") for c in r["selected"])
    assert sum(1 for _, why in r["excluded"] if why == "backlog_not_fresh") == len(history)


def test_one_post_per_company_led_by_the_preferred_event_and_counting_independent_families():
    multi = [ev("MLT", "fcf_turnaround"), ev("MLT", "operating_leverage_inflection"), ev("MLT", "margin_expansion"), ev("MLT", "profitability_turnaround")]
    r = S.select(multi + [ev("ONE", "profitability_turnaround")], SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["MLT", "ONE"]
    c = r["selected"][0]
    assert c.lead["event_type"] == "profitability_turnaround" and c.families == ["cash", "net_income", "operating"] and len(c.events) == 4
    assert c.update.label == "Net income turned positive"


def test_ranking_order():
    evs = [ev("FCF", "fcf_turnaround", cap=9e10),                         # not a preferred family: last
           ev("REV", "revenue_acceleration"),
           ev("MAR", "margin_expansion"),
           ev("NIS", "profitability_turnaround", metrics={**METRICS["profitability_turnaround"], "ni_swing_pct_rev": 0.05}),
           ev("NIB", "profitability_turnaround"),
           ev("TWO", "margin_expansion"), ev("TWO", "revenue_acceleration")]   # two preferred families beat one
    r = S.select(evs, SESSION, {}, max_posts=10)
    assert [c.symbol for c in r["selected"]] == ["TWO", "NIB", "NIS", "MAR", "REV", "FCF"]
    assert [c.symbol for c in S.select(evs, SESSION, {})["selected"]] == ["TWO", "NIB", "NIS"]
    assert [c.symbol for c in S.select(evs, SESSION, {})["outranked"]] == ["MAR", "REV", "FCF"]


def test_cooldown_and_determinism():
    evs = [ev(s, k) for s in ("AAA", "BBB", "CCC", "DDD") for k in ("margin_expansion", "revenue_acceleration")]
    r = S.select(evs, SESSION, {"AAA": date(2026, 10, 20)})
    assert [c.symbol for c in r["cooldown"]] == ["AAA"] and "AAA" not in [c.symbol for c in r["selected"]]
    assert "AAA" in [c.symbol for c in S.select(evs, SESSION, {"AAA": date(2026, 9, 1)})["selected"]]   # cooldown over
    shuffled = evs[:]
    random.Random(7).shuffle(shuffled)
    assert [c.symbol for c in S.select(shuffled, SESSION, {})["selected"]] == [c.symbol for c in S.select(evs, SESSION, {})["selected"]]


def test_simulation_maps_detection_to_the_previous_session():
    from inflection.update_simulate import session_of
    assert session_of(date(2026, 11, 3)) == date(2026, 11, 2)
    assert session_of(date(2026, 11, 7)) == date(2026, 11, 6)
    assert session_of(date(2026, 11, 9)) == date(2026, 11, 6)       # Monday's date -> Friday's session


# same-quarter feature metrics (features.compute_features): operating income 50 on revenue 500, a year earlier margin 4% on revenue 400 (= 16)
SUPPORTED = {"revenue": 500e6, "revenue_py": 400e6, "rev_yoy": 0.25, "operating_income": 50e6, "op_margin_py": 0.04, "op_margin_yoy_bps": 600.0,
             "net_income": 30e6, "net_income_py": -10e6, "fcf_margin_yoy_pp": 3.0}
# the net-income swing came from below the operating line: operating income FELL (50 vs 60), net income rose 40
NON_OPERATING = dict(SUPPORTED, op_margin_py=0.15, op_margin_yoy_bps=-500.0, rev_yoy=0.25)


def test_support_signals_and_non_operating_share():
    assert S.support(SUPPORTED) == {"operating_income": True, "operating_margin": True, "revenue_growth": True, "cash_flow": True}
    assert S.non_operating_share(SUPPORTED) == pytest.approx((40e6 - 34e6) / 40e6)
    assert S.non_operating_share(NON_OPERATING) == pytest.approx((40e6 + 10e6) / 40e6)
    # missing inputs are unknown, never support (banks and insurers report no operating income)
    assert S.support({"rev_yoy": 0.1}) == {"operating_income": None, "operating_margin": None, "revenue_growth": True, "cash_flow": None}
    assert S.non_operating_share({"net_income": 5, "net_income_py": -5}) is None
    assert S.non_operating_share(dict(SUPPORTED, net_income=-20e6)) is None          # no improvement: no share


def test_at_most_two_posts_per_lead_type_and_empty_slots_are_not_filled():
    nis = [ev(f"N{i}", "profitability_turnaround", cap=1e9 * (5 - i), feature_metrics=SUPPORTED) for i in range(4)]
    r = S.select(nis, SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["N0", "N1"] and [c.symbol for c in r["type_capped"]] == ["N2", "N3"] and r["outranked"] == []
    r = S.select(nis + [ev("MAR", "margin_expansion", cap=4e8, feature_metrics=SUPPORTED)], SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["N0", "N1", "MAR"]


def test_net_income_turnaround_mostly_below_the_operating_line():
    # alone: held, not posted, the slot stays empty
    r = S.select([ev("ONE", "profitability_turnaround", feature_metrics=NON_OPERATING)], SESSION, {})
    assert r["selected"] == [] and [(c.symbol, why) for c, why in r["held"]] == [("ONE", "net_income_mostly_non_operating")]
    # with an operating inflection: the operating event leads and net income is not counted as a family
    r = S.select([ev("TWO", "profitability_turnaround", feature_metrics=NON_OPERATING), ev("TWO", "revenue_acceleration", feature_metrics=NON_OPERATING)], SESSION, {})
    c = r["selected"][0]
    assert c.lead["event_type"] == "revenue_acceleration" and c.families == ["revenue"] and "net_income_mostly_non_operating" in c.flags


def test_an_extreme_swing_does_not_outrank_operational_support_by_size():
    big = dict(METRICS["profitability_turnaround"], ni_swing_pct_rev=5.68)                  # +568 points of revenue
    weak = dict(SUPPORTED, op_margin_yoy_bps=10.0, fcf_margin_yoy_pp=-2.0)                  # 2 of 4 support signals
    r = S.select([ev("XTR", "profitability_turnaround", metrics=big, feature_metrics=weak), ev("SUP", "profitability_turnaround", feature_metrics=SUPPORTED)],
                 SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["SUP", "XTR"] and "extreme_net_income_swing" in r["selected"][1].flags
    # equal support: above the cap the size no longer counts, market cap decides
    r = S.select([ev("XTR", "profitability_turnaround", metrics=big, cap=1e9, feature_metrics=SUPPORTED),
                  ev("CAP", "profitability_turnaround", metrics=dict(big, ni_swing_pct_rev=0.6), cap=5e9, feature_metrics=SUPPORTED)], SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["CAP", "XTR"]


def test_selection_ignores_prices_returns_and_scores():
    evs = [ev(s, k, feature_metrics=SUPPORTED) for s in ("AAA", "BBB", "CCC", "DDD") for k in ("margin_expansion", "profitability_turnaround")]
    base = [c.symbol for c in S.select(evs, SESSION, {})["selected"]]
    noisy = [dict(e, strength=99 - i, severity="high", inflection_score=i,
                  context=dict(e["context"], reaction={"excess_return": i}, excess_vs_sector_since_publication=-i)) for i, e in enumerate(evs)]
    assert [c.symbol for c in S.select(noisy, SESSION, {})["selected"]] == base
