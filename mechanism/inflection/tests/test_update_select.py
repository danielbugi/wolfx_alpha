"""Fundamental Updates night selection (update_select): every exclusion rule, the backlog guard, one post per company, the cap of 3, the cap of 2
per lead type (empty slots allowed), operational support, the non-operating divergence heuristic and its edge cases, the magnitude cap, the ranking order, the cooldown,
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


def test_support_signals_and_non_operating_divergence():
    assert S.support(SUPPORTED) == {"operating_income": True, "operating_margin": True, "revenue_growth": True, "cash_flow": True}
    assert S.non_operating_divergence(SUPPORTED) == pytest.approx((40e6 - 34e6) / 40e6)
    assert S.non_operating_divergence(NON_OPERATING) == pytest.approx((40e6 + 10e6) / 40e6)
    # missing inputs are unknown, never support (banks and insurers report no operating income)
    assert S.support({"rev_yoy": 0.1}) == {"operating_income": None, "operating_margin": None, "revenue_growth": True, "cash_flow": None}
    assert S.non_operating_divergence({"net_income": 5, "net_income_py": -5}) is None
    assert S.non_operating_divergence(dict(SUPPORTED, net_income=-20e6)) is None          # no improvement: undefined


def test_at_most_two_posts_per_lead_type_and_empty_slots_are_not_filled():
    nis = [ev(f"N{i}", "profitability_turnaround", cap=1e9 * (5 - i), feature_metrics=SUPPORTED) for i in range(4)]
    r = S.select(nis, SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["N0", "N1"] and [c.symbol for c in r["type_capped"]] == ["N2", "N3"] and r["outranked"] == []
    r = S.select(nis + [ev("MAR", "margin_expansion", cap=4e8, feature_metrics=SUPPORTED)], SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["N0", "N1", "MAR"]


def test_net_income_turnaround_mostly_below_the_operating_line():
    # alone: held, not posted, the slot stays empty
    r = S.select([ev("ONE", "profitability_turnaround", feature_metrics=NON_OPERATING)], SESSION, {})
    assert r["selected"] == [] and [(c.symbol, why) for c, why in r["held"]] == [("ONE", "non_operating_divergence")]
    # with an operating inflection: the operating event leads and net income is not counted as a family
    r = S.select([ev("TWO", "profitability_turnaround", feature_metrics=NON_OPERATING), ev("TWO", "revenue_acceleration", feature_metrics=NON_OPERATING)], SESSION, {})
    c = r["selected"][0]
    assert c.lead["event_type"] == "revenue_acceleration" and c.families == ["revenue"] and "non_operating_divergence" in c.flags


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


M = 1e6


def fm(ni, ni_py, oi, oi_py, rev=100 * M, rev_py=100 * M):
    """Feature metrics for one quarter from plain figures (prior-year operating income is stored as margin x revenue, as features.py does)."""
    return {"net_income": ni, "net_income_py": ni_py, "operating_income": oi, "revenue": rev, "revenue_py": rev_py,
            "op_margin_py": None if oi_py is None or not rev_py or rev_py <= 0 else oi_py / rev_py}


@pytest.mark.parametrize("label,metrics,expected", [
    # sign changes and negative values
    ("operating loss narrowed, net income turned positive", fm(5 * M, -50 * M, -10 * M, -40 * M), (55 - 30) / 55),
    ("operating income turned positive and explains more than all of it", fm(5 * M, -40 * M, 20 * M, -30 * M), (45 - 50) / 45),
    ("operating income fell into a loss while net income turned positive", fm(10 * M, -5 * M, -20 * M, 10 * M), (15 + 30) / 15),
    ("both still losses, net loss narrowed below the line", fm(-10 * M, -50 * M, -45 * M, -55 * M), (40 - 10) / 40),
    ("unchanged operating income: all of it is non-operating", fm(30 * M, -10 * M, 50 * M, 50 * M), 1.0),
    ("fully operational improvement after a 21% tax: negative", fm(0.79 * 40 * M, 0, 60 * M, 20 * M), (31.6 - 40) / 31.6),
    # undefined: never holds, never demotes
    ("net income fell", fm(10 * M, 20 * M, 50 * M, 10 * M), None),
    ("net income unchanged", fm(10 * M, 10 * M, 50 * M, 10 * M), None),
    ("prior-year revenue zero", fm(10 * M, -10 * M, 5 * M, None, rev_py=0), None),
    ("prior-year revenue negative", dict(fm(10 * M, -10 * M, 5 * M, 1 * M), revenue_py=-5 * M), None),
    ("operating income missing (bank)", dict(fm(10 * M, -10 * M, 5 * M, 1 * M), operating_income=None), None),
    ("infinite input", dict(fm(10 * M, -10 * M, 5 * M, 1 * M), net_income=float("inf")), None),
    ("not-a-number input", dict(fm(10 * M, -10 * M, 5 * M, 1 * M), op_margin_py=float("nan")), None),
    ("boolean is not a number", dict(fm(10 * M, -10 * M, 5 * M, 1 * M), net_income_py=False), None),
])
def test_non_operating_divergence_edge_cases(label, metrics, expected):
    got = S.non_operating_divergence(metrics)
    assert (got is None) if expected is None else got == pytest.approx(expected), label


def _ni(sym, metrics, **over):
    return ev(sym, "profitability_turnaround", feature_metrics=metrics, **over)


def test_divergence_threshold_is_strictly_above_one_half():
    at = fm(40 * M, -10 * M, 45 * M, 20 * M)                                       # dNI 50, dOI 25: exactly 0.5
    above = fm(40 * M, -10 * M, 44 * M, 20 * M)                                    # dNI 50, dOI 24: 0.52
    assert S.non_operating_divergence(at) == pytest.approx(0.5) and S.non_operating_divergence(above) == pytest.approx(0.52)
    assert [c.symbol for c in S.select([_ni("ATT", at)], SESSION, {})["selected"]] == ["ATT"]
    assert [(c.symbol, why) for c, why in S.select([_ni("ABV", above)], SESSION, {})["held"]] == [("ABV", "non_operating_divergence")]


def test_undefined_divergence_never_holds_or_flags():
    bank = dict(fm(10 * M, -10 * M, None, None), rev_yoy=0.05)                     # no operating income at all
    r = S.select([_ni("BNK", bank)], SESSION, {})
    c = r["selected"][0]
    assert c.non_operating_divergence is None and "non_operating_divergence" not in c.flags and r["held"] == []


def test_divergence_only_concerns_net_income_turnarounds():
    # a margin-led company whose net income moved below the line is neither held nor flagged: the heuristic judges only net-income events
    high = fm(40 * M, -10 * M, 10 * M, 30 * M)
    r = S.select([ev("MAR", "margin_expansion", feature_metrics=high)], SESSION, {})
    assert [c.symbol for c in r["selected"]] == ["MAR"] and r["selected"][0].flags == [] and r["held"] == []
    assert r["selected"][0].non_operating_divergence > 1                          # still recorded, for the report


def test_the_decision_record_keeps_every_event_with_its_outcome_and_reason():
    """Every input event gets exactly one row; selection and exclusion reasons and the policy in force are kept for later analysis."""
    import json
    evs = [ev("MLT", "profitability_turnaround", feature_metrics=SUPPORTED), ev("MLT", "margin_expansion", feature_metrics=SUPPORTED)]   # net-income lead
    evs += [ev(f"N{i}", "profitability_turnaround", cap=1e9 * (5 - i), feature_metrics=SUPPORTED) for i in range(3)]      # N1, N2: type cap
    evs += [ev("FCF", "fcf_turnaround", cap=9e8), ev("OUT", "fcf_turnaround", cap=4e8)]                                     # 3rd post; outranked
    evs += [ev("HLD", "profitability_turnaround", feature_metrics=NON_OPERATING)]                                            # held
    evs += [ev("COO", "margin_expansion")]                                                                                     # cooldown
    evs += [ev("OLD", available_at="2026-11-01"), ev("SML", cap=1e8), ev("LRP", "improvement_with_limited_repricing")]        # excluded
    r = S.select(evs, SESSION, {"COO": date(2026, 10, 30)})
    rows = S.decisions(r, SESSION)
    assert len(rows) == len(evs) and len({(x["symbol"], x["event_type"]) for x in rows}) == len(evs)
    got = {(x["symbol"], x["event_type"]): (x["outcome"], x["reason"]) for x in rows}
    assert got[("MLT", "profitability_turnaround")] == ("selected", "selected") and got[("MLT", "margin_expansion")] == ("selected", "selected")
    assert got[("N0", "profitability_turnaround")] == ("selected", "selected") and got[("FCF", "fcf_turnaround")] == ("selected", "selected")
    assert got[("N1", "profitability_turnaround")] == got[("N2", "profitability_turnaround")] == ("type_capped", "type_mix_cap")
    assert got[("OUT", "fcf_turnaround")] == ("outranked", "post_limit_reached")
    assert got[("HLD", "profitability_turnaround")] == ("held", "non_operating_divergence")
    assert got[("COO", "margin_expansion")] == ("cooldown", "cooldown")
    assert got[("OLD", "margin_expansion")] == ("excluded", "backlog_not_fresh") and got[("SML", "margin_expansion")] == ("excluded", "below_size_floor")
    assert got[("LRP", "improvement_with_limited_repricing")] == ("excluded", "not_public_type")
    lead = [x for x in rows if x["symbol"] == "MLT"]
    assert sorted(x["is_lead"] for x in lead) == [False, True] and {x["rank"] for x in lead} == {1}
    assert sorted(x["rank"] for x in rows if x["outcome"] in ("selected", "type_capped", "outranked") and x["is_lead"]) == [1, 2, 3, 4, 5, 6]
    assert [x["symbol"] for x in rows if x["outcome"] == "selected" and x["is_lead"]] == ["MLT", "N0", "FCF"]    # the record lists posts first
    assert all(x["rank"] is None for x in rows if x["outcome"] in ("cooldown", "held", "excluded"))
    held = next(x for x in rows if x["symbol"] == "HLD")
    assert held["non_operating_divergence"] > S.MAX_NON_OPERATING_DIVERGENCE and "non_operating_divergence" in held["flags"]
    assert all(x["policy"] == S.policy() and x["policy"]["max_per_type"] == 2 and x["session"] == "2026-11-05" for x in rows)
    assert S.decisions(r, SESSION, max_posts=5)[0]["policy"]["max_posts"] == 5                      # overrides are recorded as used
    json.loads(json.dumps(rows))                                                                    # JSON-ready
    assert not {"price", "return", "inflection_score", "score"} & {k for x in rows for k in x}     # no prices, returns or scores


def test_the_simulation_writes_the_decision_record(tmp_path):
    from inflection import update_simulate as SIM
    evs = [ev("AAA", "margin_expansion"), ev("BBB", "revenue_acceleration", cap=1e8)]
    rows = []
    out = SIM.run(evs, [SESSION], decision_rows=rows)
    assert out[0]["slots_filled"] == 1
    assert [(x["symbol"], x["outcome"], x["reason"]) for x in rows] == [("AAA", "selected", "selected"), ("BBB", "excluded", "below_size_floor")]
