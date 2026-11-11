"""Opportunity Intelligence v0, pure: evidence families, verdicts, point-in-time exclusions, convergence, ordering and wording. No database."""
import json
import re
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from opportunity import brief as B  # noqa: E402
from opportunity import evidence as E  # noqa: E402

SESSION = date(2026, 11, 10)
UTC = timezone.utc
# Mon 11-02 .. Tue 11-10, weekdays only
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


def er(symbol, day, surprise, fetched=datetime(2026, 11, 6, 22, 0, tzinfo=UTC)):
    return {"symbol": symbol, "report_date": day, "eps_estimate": Decimal("1.00"), "eps_actual": Decimal(str(round(1 + surprise / 100, 4))),
            "surprise_pct": Decimal(str(surprise)), "fetched_at": fetched}


def bars(start=100.0, end=110.0):
    out = {d: start for d in SESSIONS}
    for d in SESSIONS[4:]:
        out[d] = end
    return out


BENCH = {d: 5000.0 for d in SESSIONS}


def world(events, earnings=(), price=None):
    price = price or {}
    syms = {e["symbol"] for e in events}
    return {"events": events, "earnings": list(earnings), "sessions": SESSIONS, "calendar_source": "test",
            "bars": {s: price.get(s, bars()) for s in syms}, "bench": BENCH}


def brief_of(rep, sym):
    return next(b for b in rep["briefs"] if b["symbol"] == sym)


# ------------------------------------------------------------- families and verdicts
def test_derived_metrics_of_one_accounting_fact_count_once():
    facts = E.fundamental_facts(GOOD, "SEC 10-Q x", "2026-11-04")
    assert [f.family for f in facts] == ["revenue", "operating", "net_income", "cash"]           # 4 facts, not 9 metrics
    assert all(f.verdict == E.SUPPORTS for f in facts)
    assert E.fundamental_verdict(facts) == E.SUPPORTS


def test_every_displayed_figure_carries_its_source_and_availability_date():
    rep = B.build(world(event("AAA"), [er("AAA", date(2026, 11, 4), 8.0)]), SESSION)
    b = brief_of(rep, "AAA")
    figs = [x for f in b["evidence"]["fundamental"] for x in f["figures"]] + b["evidence"]["surprise"]["figures"] + b["evidence"]["reaction"]["figures"]
    assert figs and all(x["source"] and x["available_at"] for x in figs)
    assert all(w["source"].startswith("SEC 10-Q") and w["available_at"] for w in b["what_changed"])
    assert b["filing"]["accepted_at"] == "2026-11-04T21:05:00+00:00"


def test_a_non_operating_net_income_jump_is_held_and_shown_as_a_caution():
    m = dict(GOOD, operating_income=GOOD["op_margin_py"] * GOOD["revenue_py"] + 1e6)          # operating income barely moved
    facts = E.fundamental_facts(m, "s", "d")
    ni = next(f for f in facts if f.family == "net_income")
    assert ni.verdict == E.HELD and "not matched by operating income" in ni.note
    cons = E.contradictions(facts, E.surprise_fact(None), E.Fact("reaction", E.UNAVAILABLE))
    assert {"family": "net_income", "kind": "caution", "text": ni.note} in cons


def test_falling_revenue_is_contradicting_evidence():
    facts = E.fundamental_facts(dict(GOOD, rev_yoy=-0.08), "s", "d")
    assert next(f for f in facts if f.family == "revenue").verdict == E.CONTRADICTS
    assert E.fundamental_verdict(facts) == E.MIXED


@pytest.mark.parametrize("sp,verdict", [(8.0, E.SUPPORTS), (2.0, E.SUPPORTS), (1.9, E.NEUTRAL), (-1.9, E.NEUTRAL), (-2.0, E.CONTRADICTS), (None, E.UNAVAILABLE)])
def test_surprise_bands(sp, verdict):
    row = er("X", date(2026, 11, 4), 0.0)
    row["surprise_pct"] = None if sp is None else Decimal(str(sp))                              # Decimal, as psycopg2 returns it
    assert E.surprise_fact(row).verdict == verdict


def test_reaction_window_covers_a_release_before_the_open_and_after_the_close():
    assert E.reaction_window(SESSIONS, date(2026, 11, 4)) == (date(2026, 11, 3), date(2026, 11, 6))
    assert E.reaction_window(SESSIONS, date(2026, 11, 7)) is None                                 # a Saturday release: only 2 sessions left by the session
    assert E.reaction_window(SESSIONS, date(2026, 11, 9)) is None                                 # window not complete at the session


def test_reaction_is_measured_against_the_sp500():
    f = E.reaction_fact("AAA", SESSIONS, date(2026, 11, 4), bars(100, 110), {**BENCH, date(2026, 11, 6): 5250.0})
    assert f.verdict == E.SUPPORTS and round(f.figures[2].value, 6) == 5.0                      # +10% vs +5%
    assert E.reaction_fact("AAA", SESSIONS, date(2026, 11, 4), bars(100, 100.5), BENCH).verdict == E.NEUTRAL


# ------------------------------------------------------------- point in time
def test_an_event_detected_after_the_knowledge_cutoff_is_excluded():
    late = event("LATE", detected=B.cutoff(SESSION) + timedelta(minutes=1))
    rep = B.build(world(event("AAA") + late), SESSION)
    assert [b["symbol"] for b in rep["briefs"]] == ["AAA"] and rep["pit_exclusions"] == {"event_detected_after_cutoff": 2}


def test_a_revised_event_or_recomputed_figures_are_not_used():
    rev = event("REV", updated=B.cutoff(SESSION) + timedelta(hours=1))
    rec = event("REC", computed=B.cutoff(SESSION) + timedelta(hours=1))
    rep = B.build(world(rev + rec), SESSION)
    assert rep["pit_exclusions"]["event_revised_after_cutoff"] == 2
    b = brief_of(rep, "REC")
    assert b["convergence"]["classes"]["fundamental"] == E.UNAVAILABLE and not b["convergence"]["converging"]


def test_a_vendor_row_refreshed_after_the_cutoff_is_not_used():
    row = er("AAA", date(2026, 11, 4), 8.0, fetched=B.cutoff(SESSION) + timedelta(seconds=1))
    rep = B.build(world(event("AAA"), [row]), SESSION)
    s = brief_of(rep, "AAA")["evidence"]["surprise"]
    assert s["verdict"] == E.UNAVAILABLE and "cannot be proven" in s["note"] and rep["pit_exclusions"] == {"earnings_refetched_after_cutoff": 1}


def test_no_price_after_the_session_is_read():
    w = world(event("AAA"))
    w["sessions"] = SESSIONS + [date(2026, 11, 11)]
    w["bars"]["AAA"][date(2026, 11, 11)] = 1000.0                                                 # a later bar must not matter
    assert B.build(w, SESSION) == B.build(world(event("AAA")), SESSION)


def test_the_earnings_report_matched_is_this_quarters():
    rows = [er("AAA", date(2026, 7, 30), -9.0), er("AAA", date(2026, 11, 4), 8.0), er("AAA", date(2027, 2, 1), 0.0)]
    m = B.match_earnings(rows, date(2026, 9, 30), date(2026, 11, 4), SESSION)
    assert m["report_date"] == date(2026, 11, 4)


# ------------------------------------------------------------- convergence and order
def test_converging_needs_a_supported_business_change_plus_another_class_and_no_contradiction():
    weak = dict(GOOD, rev_yoy=0.01, free_cash_flow=None, fcf_margin_yoy_pp=None, net_income=None)  # only operating improves
    rep = B.build(world(event("ALL") + event("TWO") + event("NEG") + event("WEAK", metrics=weak),
                        [er("ALL", date(2026, 11, 4), 8.0), er("TWO", date(2026, 11, 4), 0.5), er("NEG", date(2026, 11, 4), -6.0),
                         er("WEAK", date(2026, 11, 4), 8.0)]), SESSION)
    conv = {b["symbol"]: b["convergence"] for b in rep["briefs"]}
    assert conv["ALL"]["supporting"] == ["fundamental", "surprise", "reaction"] and conv["ALL"]["converging"]
    assert conv["TWO"]["converging"] and conv["TWO"]["classes"]["surprise"] == E.NEUTRAL
    assert not conv["NEG"]["converging"] and conv["NEG"]["contradicting"] == ["surprise"]
    assert not conv["WEAK"]["converging"]                                                         # surprise + reaction alone are not a business change
    assert [b["symbol"] for b in rep["briefs"]] == ["ALL", "TWO", "NEG", "WEAK"]
    assert {"family": "surprise", "kind": "contradicts", "text": "EPS versus the consensus estimate went the other way"} in brief_of(rep, "NEG")["contradictions"]


def test_the_order_is_deterministic_and_does_not_depend_on_input_order():
    evs = event("BBB") + event("AAA") + event("CCC", accepted=datetime(2026, 11, 3, 21, 0, tzinfo=UTC))
    a = B.build(world(evs), SESSION)
    b = B.build(world(list(reversed(evs))), SESSION)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert [x["symbol"] for x in a["briefs"]] == ["AAA", "BBB", "CCC"]                          # same evidence: newest first, then symbol


def test_price_built_event_types_are_not_a_business_change():
    assert "improvement_with_limited_repricing" not in E.HEADLINE_TYPES


# ------------------------------------------------------------- no score, no prediction
def test_no_score_and_no_predictive_wording():
    rep = B.build(world(event("AAA"), [er("AAA", date(2026, 11, 4), 8.0)]), SESSION)
    text = json.dumps(rep, default=str).lower()
    for word in (r'"score"', r"rating", r"probabilit", r"expected_return", r"forecast"):
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
