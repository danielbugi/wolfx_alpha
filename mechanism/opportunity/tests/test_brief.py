"""Opportunity Intelligence v1, pure: the three separate dimensions (business, expectations, market response), the pre-release rule for the
consensus, distorted price windows, point-in-time exclusions, categories, ordering and wording. No database."""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from opportunity import brief as B  # noqa: E402
from opportunity import evidence as E  # noqa: E402

SESSION = date(2026, 11, 10)
UTC = timezone.utc
SESSIONS = [date(2026, 10, 30), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)]

GOOD = {"revenue": 972e6, "revenue_py": 749e6, "rev_yoy": 0.2977, "rev_accel_pp": 21.1, "rev_qoq": 0.158,
        "operating_income": 266e6, "op_margin": 0.2737, "op_margin_py": 0.1362, "op_margin_yoy_bps": 1375.0, "op_leverage": 5.4,
        "net_income": 78e6, "net_income_py": -10e6, "free_cash_flow": 99e6, "free_cash_flow_py": -11e6,
        "fcf_margin": 0.1019, "fcf_margin_py": -0.0147, "fcf_margin_yoy_pp": 11.65, "cash_conversion": 1.7}


def event(symbol, *, types=("revenue_acceleration", "margin_expansion"), metrics=GOOD, accepted=datetime(2026, 11, 4, 21, 5, tzinfo=UTC),
          detected=datetime(2026, 11, 5, 0, 30, tzinfo=UTC), updated=None, computed=None, quarter_end=date(2026, 9, 30)):
    return [{"id": i, "symbol": symbol, "quarter_end": quarter_end, "event_type": t, "severity": "high", "available_at": accepted.date(),
             "availability_basis": "sec_acceptance", "first_detected_at": detected, "updated_at": updated or detected, "revision_count": 0,
             "explanation": [f"{t} line"], "feature_metrics": metrics, "feature_computed_at": computed or detected,
             "accession": "0000000000-26-000001", "form": "10-Q", "accepted_at": accepted, "filed_date": accepted.date(), "company_name": symbol + " Inc."}
            for i, t in enumerate(types)]


def er(symbol, day, surprise, fetched=datetime(2026, 11, 6, 22, 0, tzinfo=UTC), actual="1.08"):
    return {"symbol": symbol, "report_date": day, "eps_estimate": Decimal("1.00"), "eps_actual": None if actual is None else Decimal(actual),
            "surprise_pct": Decimal(str(surprise)), "fetched_at": fetched}


def bars(start=100.0, end=110.0):
    out = {d: start for d in SESSIONS}
    for d in SESSIONS[4:]:
        out[d] = end
    return out


BENCH = {d: 5000.0 for d in SESSIONS}


def world(events, earnings=(), price=None, disc=(), pre_release=None):
    price = price or {}
    syms = {e["symbol"] for e in events}
    return {"events": events, "earnings": list(earnings), "sessions": SESSIONS, "calendar_source": "test", "discontinuities": list(disc),
            "pre_release": pre_release or {}, "bars": {s: price.get(s, bars()) for s in syms}, "bench": BENCH}


def brief_of(rep, sym):
    return next(b for b in rep["briefs"] if b["symbol"] == sym)


# ------------------------------------------------------------- three separate dimensions
def test_the_three_dimensions_are_reported_separately():
    rep = B.build(world(event("AAA"), [er("AAA", date(2026, 11, 4), 8.0)]), SESSION)
    b = brief_of(rep, "AAA")
    assert set(b["evidence"]) == {"business", "expectations", "market"}
    assert b["assessment"]["dimensions"] == {"business": E.SUPPORTS, "expectations": E.UNVERIFIED, "market": E.POSITIVE}


def test_derived_metrics_of_one_accounting_fact_count_once():
    facts = E.business_facts(GOOD, "SEC 10-Q x", "2026-11-04")
    assert [f.family for f in facts] == ["revenue", "operating", "net_income", "cash"]           # 4 facts, not 9 metrics
    assert E.business_verdict(facts) == E.SUPPORTS


def test_a_positive_market_response_is_not_business_evidence():
    weak = dict(GOOD, rev_yoy=0.01, free_cash_flow=None, fcf_margin_yoy_pp=None, net_income=None)  # only operating improves
    rep = B.build(world(event("WEAK", metrics=weak), price={"WEAK": bars(100, 130)}), SESSION)
    a = brief_of(rep, "WEAK")["assessment"]
    assert a["dimensions"]["market"] == E.POSITIVE and a["dimensions"]["business"] == E.MIXED
    assert a["category"] == E.CATEGORY_MIXED and not a["converging"]                                # +30% does not make it a business improvement


# ------------------------------------------------------------- expectations: verified pre-release consensus only
def test_a_consensus_stored_after_the_release_is_unverified_and_not_counted():
    f = E.expectations_fact(er("X", date(2026, 11, 4), 8.0))
    assert f.verdict == E.UNVERIFIED and "after the release" in f.note and "not counted" in f.note
    assert all("unverified" in x.source for x in f.figures)


def test_a_genuine_pre_release_snapshot_enables_the_surprise():
    pre = {"eps_estimate": Decimal("1.00"), "observed_at": datetime(2026, 11, 3, 20, 0, tzinfo=UTC), "source": "consensus snapshot"}
    f = E.expectations_fact(er("X", date(2026, 11, 4), 0.0, actual="1.08"), pre)
    assert f.verdict == E.SUPPORTS and round(f.figures[2].value, 6) == 8.0
    late = dict(pre, observed_at=datetime(2026, 11, 4, 6, 0, tzinfo=UTC))                            # 01:00 New York on the release day: too late
    assert E.expectations_fact(er("X", date(2026, 11, 4), 0.0), late).verdict == E.UNVERIFIED


@pytest.mark.parametrize("actual,verdict", [("1.08", E.SUPPORTS), ("1.01", E.NEUTRAL), ("0.95", E.CONTRADICTS)])
def test_verified_surprise_bands(actual, verdict):
    pre = {"eps_estimate": Decimal("1.00"), "observed_at": datetime(2026, 11, 1, tzinfo=UTC)}
    assert E.expectations_fact(er("X", date(2026, 11, 4), 0.0, actual=actual), pre).verdict == verdict


def test_no_reported_eps_is_unavailable():
    assert E.expectations_fact(er("X", date(2026, 11, 4), 0.0, actual=None)).verdict == E.UNAVAILABLE


# ------------------------------------------------------------- market response: distorted windows
def test_a_split_like_move_inside_the_window_is_unreliable_not_counted():
    p = bars(100, 100)
    p[date(2026, 11, 5)] = p[date(2026, 11, 6)] = 50.0                                              # a 2:1 split on 11-05, unadjusted
    f = E.market_fact("SPL", SESSIONS, date(2026, 11, 4), p, BENCH)
    assert f.verdict == E.UNRELIABLE and "split ratio" in f.note and f.figures                       # figures still shown, flagged
    rep = B.build(world(event("SPL"), price={"SPL": p}), SESSION)
    a = brief_of(rep, "SPL")["assessment"]
    assert a["category"] == E.CATEGORY_UNCHECKED and "market" not in a["agreeing"] + a["disagreeing"]
    assert any(c["family"] == "market" and c["kind"] == "caution" for c in brief_of(rep, "SPL")["contradictions"])


def test_a_detected_discontinuity_known_by_the_cutoff_flags_the_window():
    d = {"symbol": "AAA", "date": date(2026, 11, 5), "kind": "jump_up", "ratio": Decimal("4.093"), "detected_at": datetime(2026, 11, 6, 1, 0)}
    rep = B.build(world(event("AAA"), disc=[d]), SESSION)
    assert brief_of(rep, "AAA")["assessment"]["dimensions"]["market"] == E.UNRELIABLE
    late = dict(d, detected_at=datetime(2026, 11, 12, 1, 0))                                         # found only after the cutoff: not known then
    assert brief_of(B.build(world(event("AAA"), disc=[late]), SESSION), "AAA")["assessment"]["dimensions"]["market"] == E.POSITIVE


def test_an_ordinary_large_move_is_not_flagged():
    assert E.split_like(1.40) is None and E.split_like(0.70) is None
    assert E.split_like(1.25) is None and E.split_like(0.80) is None and E.split_like(0.667) is None   # earnings-size moves (real data) are not splits
    assert E.split_like(0.5) == 0.5 and E.split_like(3.05) == 3.0 and E.split_like(0.1) == pytest.approx(0.1)


def test_a_missing_bar_in_the_window_is_unavailable():
    p = bars()
    del p[date(2026, 11, 5)]
    assert E.market_fact("X", SESSIONS, date(2026, 11, 4), p, BENCH).verdict == E.UNAVAILABLE


def test_the_market_window_and_bands():
    assert E.reaction_window(SESSIONS, date(2026, 11, 4)) == (date(2026, 11, 3), date(2026, 11, 6))
    assert E.reaction_window(SESSIONS, date(2026, 11, 9)) is None
    f = E.market_fact("AAA", SESSIONS, date(2026, 11, 4), bars(100, 110), {**BENCH, date(2026, 11, 6): 5250.0})
    assert f.verdict == E.POSITIVE and round(f.figures[2].value, 6) == 5.0
    assert E.market_fact("AAA", SESSIONS, date(2026, 11, 4), bars(100, 100.5), BENCH).verdict == E.FLAT


# ------------------------------------------------------------- categories, discovery view, ordering
def test_categories_including_the_discovery_view():
    rep = B.build(world(event("UP") + event("FLAT") + event("DOWN") + event("SPL"),
                        price={"FLAT": bars(100, 100.2), "DOWN": bars(100, 90), "SPL": {**bars(100, 100), date(2026, 11, 5): 50.0, date(2026, 11, 6): 50.0}}), SESSION)
    cat = {b["symbol"]: b["assessment"]["category"] for b in rep["briefs"]}
    assert cat == {"UP": E.CATEGORY_CONFIRMED, "FLAT": E.CATEGORY_DISCOVERY, "DOWN": E.CATEGORY_DISCOVERY, "SPL": E.CATEGORY_UNCHECKED}
    assert rep["counts"]["by_category"][E.CATEGORY_DISCOVERY] == 2 and rep["counts"]["market_unreliable"] == 1
    assert [b["symbol"] for b in rep["briefs"]] == ["UP", "FLAT", "SPL", "DOWN"]                    # ordering unchanged: agreement first, disagreement last
    assert any(c["text"].startswith("The price fell") for c in brief_of(rep, "DOWN")["contradictions"])


def test_a_non_operating_net_income_jump_is_held_and_shown_as_a_caution():
    m = dict(GOOD, operating_income=GOOD["op_margin_py"] * GOOD["revenue_py"] + 1e6)
    facts = E.business_facts(m, "s", "d")
    ni = next(f for f in facts if f.family == "net_income")
    assert ni.verdict == E.HELD and "not matched by operating income" in ni.note
    assert {"family": "net_income", "kind": "caution", "text": ni.note} in E.contradictions(facts, E.expectations_fact(None), E.Fact("market", E.UNAVAILABLE))


def test_the_order_is_deterministic_and_does_not_depend_on_input_order():
    evs = event("BBB") + event("AAA") + event("CCC", accepted=datetime(2026, 11, 3, 21, 0, tzinfo=UTC))
    a, b = B.build(world(evs), SESSION), B.build(world(list(reversed(evs))), SESSION)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert [x["symbol"] for x in a["briefs"]] == ["AAA", "BBB", "CCC"]


# ------------------------------------------------------------- point in time and attribution
def test_every_displayed_figure_carries_its_source_and_availability_date():
    rep = B.build(world(event("AAA"), [er("AAA", date(2026, 11, 4), 8.0)]), SESSION)
    b = brief_of(rep, "AAA")
    figs = [x for f in b["evidence"]["business"] for x in f["figures"]] + b["evidence"]["expectations"]["figures"] + b["evidence"]["market"]["figures"]
    assert figs and all(x["source"] and x["available_at"] for x in figs)
    assert b["filing"]["accepted_at"] == "2026-11-04T21:05:00+00:00"


def test_late_detected_revised_or_recomputed_inputs_are_excluded():
    cut = B.cutoff(SESSION)
    rep = B.build(world(event("AAA") + event("LATE", detected=cut + timedelta(minutes=1)) + event("REV", updated=cut + timedelta(hours=1))
                        + event("REC", computed=cut + timedelta(hours=1))), SESSION)
    assert rep["pit_exclusions"] == {"event_detected_after_cutoff": 2, "event_revised_after_cutoff": 2, "feature_recomputed_after_cutoff": 1}
    assert brief_of(rep, "REC")["assessment"]["dimensions"]["business"] == E.UNAVAILABLE


def test_a_vendor_row_refreshed_after_the_cutoff_is_not_used():
    late = er("AAA", date(2026, 11, 3), 8.0, fetched=B.cutoff(SESSION) + timedelta(seconds=1))
    rep = B.build(world(event("AAA"), [late]), SESSION)
    b = brief_of(rep, "AAA")
    assert "cannot be proven" in b["evidence"]["expectations"]["note"] and rep["pit_exclusions"] == {"earnings_refetched_after_cutoff": 1}
    on_time = brief_of(B.build(world(event("AAA"), [dict(late, fetched_at=datetime(2026, 11, 6, tzinfo=UTC))]), SESSION), "AAA")
    assert b["first_public"] == on_time["first_public"] == "2026-11-03"                              # a later re-fetch does not move the market window
    assert b["evidence"]["market"] == on_time["evidence"]["market"]


def test_no_price_after_the_session_is_read():
    w = world(event("AAA"))
    w["sessions"] = SESSIONS + [date(2026, 11, 11)]
    w["bars"]["AAA"][date(2026, 11, 11)] = 1000.0
    assert B.build(w, SESSION) == B.build(world(event("AAA")), SESSION)


def test_price_built_event_types_are_not_a_business_change():
    assert "improvement_with_limited_repricing" not in E.HEADLINE_TYPES


# ------------------------------------------------------------- no score, no prediction, no writes
def test_no_score_and_no_predictive_wording_in_the_briefs():
    rep = B.build(world(event("AAA"), [er("AAA", date(2026, 11, 4), 8.0)]), SESSION)
    text = json.dumps(rep["briefs"], default=str).lower()
    for word in (r'"score"', r"\brating", r"probabilit", r"expected_return", r"forecast", r"opportunit"):
        assert not re.search(word, text), word
    assert B.wording_violations(rep) == []
    bad = dict(rep, briefs=[dict(rep["briefs"][0], what_changed=[{"lines": ["This stock should outperform"]}])])
    assert B.wording_violations(bad) == ["This stock should outperform"]


def test_the_module_never_writes_or_sends():
    root = os.path.dirname(B.__file__)
    for name in os.listdir(root):
        if name.endswith(".py"):
            src = open(os.path.join(root, name), encoding="utf-8").read()
            for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "telegram", "TelegramClient", "send_message"):
                assert forbidden not in src, (name, forbidden)
