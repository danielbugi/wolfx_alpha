"""SEC XBRL company facts -> normalized quarters. Synthetic facts with hand-computed numbers; pure (no network, no database)."""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from data_updaters import sec_statements as S  # noqa: E402

CIK = 1234567
D = date.fromisoformat


def e(end, val, accn, form, filed, start=None, fy=2024, fp="Q1"):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        out["start"] = start
    return out


Q1 = ("A1", "10-Q", "2024-05-02", 2024, "Q1")
Q2 = ("A2", "10-Q", "2024-08-01", 2024, "Q2")
Q3 = ("A3", "10-Q", "2024-11-01", 2024, "Q3")
FY = ("A4", "10-K", "2025-02-20", 2024, "FY")


def doc(tags, cik=CIK):
    return {"cik": cik, "facts": {"us-gaap": {t: {"units": {u: v}} for t, (u, v) in tags.items()}}}


def filing(period, val, start, end):
    a, form, filed, fy, fp = period
    return e(end, val, a, form, filed, start, fy, fp)


BASE = doc({
    "Revenues": ("USD", [
        filing(Q1, 100, "2024-01-01", "2024-03-31"),
        filing(Q2, 110, "2024-04-01", "2024-06-30"), filing(Q2, 210, "2024-01-01", "2024-06-30"),
        filing(Q3, 120, "2024-07-01", "2024-09-30"), filing(Q3, 330, "2024-01-01", "2024-09-30"),
        filing(FY, 460, "2024-01-01", "2024-12-31"),
    ]),
    "NetCashProvidedByUsedInOperatingActivities": ("USD", [
        filing(Q1, 20, "2024-01-01", "2024-03-31"), filing(Q2, 45, "2024-01-01", "2024-06-30"),
        filing(Q3, 70, "2024-01-01", "2024-09-30"), filing(FY, 100, "2024-01-01", "2024-12-31"),
    ]),
    "PaymentsToAcquirePropertyPlantAndEquipment": ("USD", [
        filing(Q1, 5, "2024-01-01", "2024-03-31"), filing(Q2, 12, "2024-01-01", "2024-06-30"),
        filing(Q3, 20, "2024-01-01", "2024-09-30"), filing(FY, 30, "2024-01-01", "2024-12-31"),
    ]),
    "EarningsPerShareDiluted": ("USD/shares", [
        filing(Q1, 0.50, "2024-01-01", "2024-03-31"), filing(Q2, 0.55, "2024-04-01", "2024-06-30"),
        filing(Q3, 0.60, "2024-07-01", "2024-09-30"), filing(FY, 2.30, "2024-01-01", "2024-12-31"),
    ]),
    "Assets": ("USD", [e("2024-03-31", 1000, "A1", "10-Q", "2024-05-02"), e("2024-12-31", 1200, "A4", "10-K", "2025-02-20", fy=2024, fp="FY")]),
})


def rows(d=BASE, asof=None):
    return {(r.metric, r.period_end.isoformat()): r for r in S.normalize(S.parse_companyfacts(d, CIK), asof)}


def test_reported_quarters_and_their_provenance():
    r = rows()
    q1 = r[("revenue", "2024-03-31")]
    assert (q1.value, q1.basis, q1.accession, q1.filed, q1.form) == (100, "reported", "A1", D("2024-05-02"), "10-Q")
    assert (r[("revenue", "2024-06-30")].value, r[("revenue", "2024-09-30")].value) == (110, 120)


def test_q4_is_the_annual_figure_less_the_year_to_date_and_says_so():
    q4 = rows()[("revenue", "2024-12-31")]
    assert (q4.value, q4.basis, q4.accession, q4.form) == (130, "annual_less_quarters", "A4", "10-K")   # 460 - 330
    assert (q4.fiscal_year, q4.fiscal_quarter) == (2024, 4)


def test_cash_flow_year_to_date_values_are_differenced():
    r = rows()
    assert [r[("operating_cash_flow", d)].value for d in ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31")] == [20, 25, 25, 30]
    assert [r[("operating_cash_flow", d)].basis for d in ("2024-03-31", "2024-06-30", "2024-12-31")] == ["reported", "ytd_difference", "annual_less_quarters"]
    assert [r[("capex", d)].value for d in ("2024-06-30", "2024-09-30", "2024-12-31")] == [7, 8, 10]


def test_free_cash_flow_only_when_both_parts_exist_and_is_marked_computed():
    r = rows()
    assert r[("free_cash_flow", "2024-06-30")].value == 18 and r[("free_cash_flow", "2024-06-30")].basis == "computed"
    only_ocf = doc({"NetCashProvidedByUsedInOperatingActivities": ("USD", BASE["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"])})
    assert not [k for k in rows(only_ocf) if k[0] == "free_cash_flow"]


def test_eps_is_only_ever_the_reported_figure():
    r = rows()
    assert r[("eps_diluted", "2024-06-30")].value == 0.55
    assert ("eps_diluted", "2024-12-31") not in r          # Q4 EPS cannot be derived from the annual figure


def test_balance_sheet_instants():
    r = rows()
    assert r[("total_assets", "2024-03-31")].value == 1000 and r[("total_assets", "2024-03-31")].basis == "instant"
    assert r[("total_assets", "2024-12-31")].value == 1200


def test_a_missing_component_leaves_the_quarter_absent_instead_of_estimating_it():
    no_q2 = doc({"Revenues": ("USD", [filing(Q1, 100, "2024-01-01", "2024-03-31"), filing(Q3, 330, "2024-01-01", "2024-09-30")])})
    r = rows(no_q2)
    assert ("revenue", "2024-03-31") in r and ("revenue", "2024-09-30") not in r      # 330 - 100 spans two quarters: not a quarter


AMENDED = doc({"Revenues": ("USD", BASE["facts"]["us-gaap"]["Revenues"]["units"]["USD"] + [
    filing(("A6", "10-K/A", "2025-04-10", 2024, "FY"), 465, "2024-01-01", "2024-12-31"),                       # restated annual revenue
    e("2024-03-31", 101, "A5", "10-Q", "2025-05-02", "2024-01-01", fy=2025, fp="Q1"),                       # a later filing restates Q1 2024
])})


def test_the_latest_filing_wins_and_the_earlier_value_is_still_in_the_raw_facts():
    now = rows(AMENDED)
    assert now[("revenue", "2024-12-31")].value == 135 and now[("revenue", "2024-12-31")].accession == "A6"
    assert now[("revenue", "2024-03-31")].value == 101 and now[("revenue", "2024-03-31")].accession == "A5"
    raw = [f.value for f in S.parse_companyfacts(AMENDED, CIK) if f.metric == "revenue" and f.end == D("2024-12-31")]
    assert sorted(raw) == [460, 465]


def test_point_in_time_view_ignores_what_was_filed_later():
    then = rows(AMENDED, asof=D("2025-03-01"))
    assert then[("revenue", "2024-12-31")].value == 130 and then[("revenue", "2024-03-31")].value == 100
    assert ("revenue", "2024-12-31") not in rows(AMENDED, asof=D("2025-02-01"))      # the 10-K was not public yet


def test_fiscal_label_comes_from_the_filing_that_treats_the_period_as_current():
    q1 = rows(AMENDED)[("revenue", "2024-03-31")]            # restated by a 2025 filing, whose own label is Q1 2025
    assert (q1.fiscal_year, q1.fiscal_quarter) == (2024, 1)


def test_a_document_for_another_company_is_refused_whole():
    with pytest.raises(S.SecStatementError):
        S.parse_companyfacts(doc({"Revenues": ("USD", [])}, cik=999), CIK)


def test_unusable_entries_are_skipped_not_repaired():
    junk = doc({"Revenues": ("USD", [
        e("2024-03-31", "n/a", "A1", "10-Q", "2024-05-02", "2024-01-01"),          # not a number
        e("2024-03-31", 5, "A9", "8-K", "2024-05-02", "2024-01-01"),                # not a periodic report
        e("2024-03-31", 6, "", "10-Q", "2024-05-02", "2024-01-01"),                 # no accession
        e("2024-03-31", 7, "A1", "10-Q", "2024-05-02"),                             # duration without a start
        filing(Q1, 100, "2024-01-01", "2024-03-31"),
    ])})
    got = S.parse_companyfacts(junk, CIK)
    assert [(f.value, f.accession) for f in got] == [(100.0, "A1")]


def test_the_filings_highest_priority_revenue_tag_wins_for_a_period():
    d = {"cik": CIK, "facts": {"us-gaap": {
        "SalesRevenueNet": {"units": {"USD": [filing(Q1, 90, "2024-01-01", "2024-03-31")]}},
        "Revenues": {"units": {"USD": [filing(Q1, 100, "2024-01-01", "2024-03-31")]}},
    }}}
    assert rows(d)[("revenue", "2024-03-31")].value == 100


def test_capex_falls_back_to_productive_assets_when_the_filing_has_no_ppe_tag():
    """Home Depot and Vishay report capex as PaymentsToAcquireProductiveAssets only; without the fallback their free cash flow was missing for every quarter."""
    d = {"cik": CIK, "facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [filing(Q1, 20, "2024-01-01", "2024-03-31")]}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": [filing(Q1, 5, "2024-01-01", "2024-03-31")]}},
    }}}
    r = rows(d)
    assert r[("capex", "2024-03-31")].value == 5 and r[("free_cash_flow", "2024-03-31")].value == 15
    both = {"cik": CIK, "facts": {"us-gaap": {**d["facts"]["us-gaap"], "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [filing(Q1, 7, "2024-01-01", "2024-03-31")]}}}}}
    assert rows(both)[("capex", "2024-03-31")].value == 7                 # the primary tag still wins when a filing carries both
