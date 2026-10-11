"""SEC XBRL facts -> point-in-time quarterly series -> anomaly rules. Synthetic company facts with hand-computed numbers (pure; no network)."""
import os
import re
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from market_intelligence import financial_anomaly as A  # noqa: E402
from market_intelligence import sec_financials as S  # noqa: E402

CIK = 1234567
D = date.fromisoformat


def entry(end, val, accn, form, filed, start, fy=2025, fp="Q1"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}


QUARTERS = {   # end, start, accn, filed, form
    "2024Q1": ("2024-03-31", "2024-01-01", "0000000001-24-000001", "2024-05-02", "10-Q"),
    "2024Q2": ("2024-06-30", "2024-04-01", "0000000001-24-000002", "2024-08-01", "10-Q"),
    "2024Q3": ("2024-09-30", "2024-07-01", "0000000001-24-000003", "2024-10-31", "10-Q"),
    "2025Q1": ("2025-03-31", "2025-01-01", "0000000001-25-000001", "2025-05-01", "10-Q"),
    "2025Q2": ("2025-06-30", "2025-04-01", "0000000001-25-000002", "2025-07-31", "10-Q"),
    "2025Q3": ("2025-09-30", "2025-07-01", "0000000001-25-000003", "2025-10-30", "10-Q"),
}
ANNUAL_2024 = ("2024-12-31", "2024-01-01", "0000000001-25-000009", "2025-02-14", "10-K")
REV = {"2024Q1": 100, "2024Q2": 105, "2024Q3": 110, "2025Q1": 130, "2025Q2": 135, "2025Q3": 170}
NI = {"2024Q1": 10, "2024Q2": 10, "2024Q3": 11, "2025Q1": 13, "2025Q2": 14, "2025Q3": 40}
OI = {"2024Q1": 15, "2024Q2": 16, "2024Q3": 17, "2025Q1": 20, "2025Q2": 21, "2025Q3": 44}


def make_doc(rev=REV, ni=NI, oi=OI, extra=None, cik=CIK, annual=(440, 44, 68)):
    def rows(vals, ann):
        out = [entry(q[0], vals[k], q[2], q[4], q[3], q[1]) for k, q in QUARTERS.items() if k in vals]
        a = ANNUAL_2024
        out.append(entry(a[0], ann, a[2], a[4], a[3], a[1], fy=2024, fp="FY"))
        return out
    units = {"Revenues": {"units": {"USD": rows(rev, annual[0])}}, "NetIncomeLoss": {"units": {"USD": rows(ni, annual[1])}}, "OperatingIncomeLoss": {"units": {"USD": rows(oi, annual[2])}}}
    for tag, e in (extra or []):
        units.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(e)
    return {"cik": cik, "entityName": "Synthetic Co", "facts": {"us-gaap": units}}


def facts_of(doc):
    return S.parse_companyfacts(doc, CIK)


# ---------------------------------------------------------------------------------------------------------------------------------- parsing
def test_a_document_for_another_cik_is_refused_whole():
    with pytest.raises(S.FinancialFactError, match="refused as a whole"):
        S.parse_companyfacts(make_doc(cik=999), CIK)


def test_values_are_verbatim_and_unusable_entries_are_skipped_not_repaired():
    doc = make_doc(extra=[("Revenues", {"start": "2025-01-01", "end": "2025-03-31", "val": "n/a", "accn": "x", "form": "10-Q", "filed": "2025-05-01"}),
                          ("Revenues", {"start": "2025-01-01", "end": "2025-03-31", "val": 1, "form": "10-Q", "filed": "2025-05-01"})])
    fs = facts_of(doc)
    assert all(isinstance(f.value, float) for f in fs)
    assert not [f for f in fs if f.value == 1.0]                                  # no accession -> skipped
    assert {f.value for f in fs if f.group == "revenue" and f.end == D("2025-03-31")} == {130.0}


# ---------------------------------------------------------------------------------------------------------------------------------- series
def test_q4_is_derived_from_the_annual_figure_and_flagged():
    q = S.quarter_series(facts_of(make_doc()), "revenue", D("2025-03-01"))
    q4 = q[D("2024-12-31")]
    assert q4.value == 125.0 and q4.derived and q4.accession == ANNUAL_2024[2]       # 440 - (100 + 105 + 110)
    assert not q[D("2024-09-30")].derived


def test_a_series_contains_only_what_was_filed_by_the_as_of_date():
    s = S.quarter_series(facts_of(make_doc()), "revenue", D("2025-05-15"))
    assert D("2025-03-31") in s and D("2025-06-30") not in s and D("2025-09-30") not in s


def test_q4_is_not_derived_when_a_quarter_is_missing():
    rev = {k: v for k, v in REV.items() if k != "2024Q2"}
    assert D("2024-12-31") not in S.quarter_series(facts_of(make_doc(rev=rev)), "revenue", D("2025-03-01"))


def test_a_restatement_filed_after_the_decision_date_is_invisible():
    restated = ("Revenues", entry("2024-09-30", 90, "0000000001-25-000077", "10-Q/A", "2025-11-20", "2024-07-01"))
    fs = facts_of(make_doc(extra=[restated]))
    assert S.quarter_series(fs, "revenue", D("2025-10-30"))[D("2024-09-30")].value == 110.0      # as known at the 2025Q3 filing
    assert S.quarter_series(fs, "revenue", D("2025-12-01"))[D("2024-09-30")].value == 90.0       # known only later


# ---------------------------------------------------------------------------------------------------------------------------------- metrics and rules
def metrics_at(doc, key="2025Q3"):
    q = QUARTERS[key]
    return A.quarter_metrics(facts_of(doc), q[2], D(q[3]))


def test_quarter_metrics_hand_computed():
    m = metrics_at(make_doc()).metrics
    assert m["revenue"] == 170 and m["revenue_prior_year"] == 110 and m["revenue_yoy"] == pytest.approx(170 / 110 - 1)
    assert m["revenue_yoy_prev_quarter"] == pytest.approx(135 / 105 - 1) and m["revenue_acceleration"] == pytest.approx((170 / 110) - (135 / 105))
    assert m["operating_margin"] == pytest.approx(44 / 170) and m["operating_margin_change"] == pytest.approx(44 / 170 - 17 / 110)


def test_metrics_use_the_prior_year_value_known_at_the_filing_not_a_later_restatement():
    restated = ("Revenues", entry("2024-09-30", 90, "0000000001-25-000077", "10-Q/A", "2025-11-20", "2024-07-01"))
    assert metrics_at(make_doc(extra=[restated])).metrics["revenue_prior_year"] == 110


def test_a_revenue_acceleration_with_operating_support_is_recurring():
    a = A.detect(metrics_at(make_doc()))
    assert a is not None and {"revenue_acceleration", "net_income_surge", "operating_margin_expansion"} <= set(a.rules)
    assert a.nature == "recurring_operating" and a.classifier == "financial_anomaly_rules" and a.classifier_version == "v1"


def test_a_net_income_jump_without_an_operating_move_is_one_time_suspected():
    a = A.detect(metrics_at(make_doc(ni={**NI, "2025Q3": 60}, oi={**OI, "2025Q3": 18})))
    assert a is not None and "net_income_surge" in a.rules and a.nature == "one_time_suspected"


def test_a_loss_to_profit_swing_is_a_turnaround():
    a = A.detect(metrics_at(make_doc(ni={**NI, "2024Q3": -5, "2025Q3": 10}, oi={**OI, "2025Q3": 17.5}), "2025Q3"))
    assert a is not None and "net_income_turnaround" in a.rules


def test_ordinary_growth_triggers_nothing():
    assert A.detect(metrics_at(make_doc(rev={**REV, "2025Q3": 118}, ni={**NI, "2025Q3": 12}, oi={**OI, "2025Q3": 18}))) is None


def test_the_quarter_before_has_no_acceleration_so_no_revenue_rule():
    a = A.detect(metrics_at(make_doc(), "2025Q2"))
    assert a is None or "revenue_acceleration" not in a.rules


def test_a_filing_with_no_matching_facts_returns_none():
    assert A.quarter_metrics(facts_of(make_doc()), "0000000001-25-999999", D("2025-10-30")) is None


# ---------------------------------------------------------------------------------------------------------------------------------- purity
def test_the_two_modules_are_pure():
    bad = re.compile(r"psycopg2|sqlalchemy|requests|urllib|yfinance|datetime\.now|date\.today|utcnow|time\.time\(|os\.environ|open\(")
    for name in ("sec_financials.py", "financial_anomaly.py"):
        src = open(os.path.join(ROOT, "mechanism", "market_intelligence", name), encoding="utf-8").read()
        code = "\n".join(l.split("  #")[0] for l in src.splitlines() if not l.strip().startswith("#"))
        assert not bad.search(code), name
