"""SEC earnings pipeline end to end on SYNTHETIC filings, XBRL facts and prices (no network): decision time, reaction bar, anomaly class and the arithmetic of reaction/drift/relative volume."""
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (ROOT, os.path.join(ROOT, "mechanism"), os.path.join(ROOT, "ml_training", "experiments"), os.path.join(ROOT, "mechanism", "market_intelligence", "tests")):
    sys.path.insert(0, p)

import sec_earnings_pipeline as P  # noqa: E402
import test_sec_financials as TF  # noqa: E402
from market_intelligence import filing_contract as F  # noqa: E402

NY = ZoneInfo("America/New_York")


def ts(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY).astimezone(timezone.utc)


def prices():
    dates = pd.bdate_range("2024-06-03", "2026-01-30")
    close = np.full(len(dates), 50.0)
    vol = np.full(len(dates), 1_000_000.0)
    k = list(dates.date).index(date(2025, 10, 29))
    close[k:] = 55.0                                       # +10% on the session after the after-close release
    vol[k] = 3_000_000.0
    opn = np.r_[50.0, close[:-1]]
    opn[k] = 55.0                                          # the session opens at the new level (a gap)
    return pd.DataFrame({"date": dates, "open": opn, "close": close, "volume": vol})


def filings():
    out = []
    for key, (end, start, accn, filed, form) in TF.QUARTERS.items():
        y, m, d = (int(x) for x in filed.split("-"))
        out.append(F.FilingFact(accession=accn, cik=TF.CIK, form_type=form, filing_date=date(y, m, d), accepted_at=ts(y, m, d, 12, 0), report_period=date.fromisoformat(end)))
    out.append(F.FilingFact(accession="0000000001-25-000050", cik=TF.CIK, form_type="8-K", filing_date=date(2025, 10, 28), accepted_at=ts(2025, 10, 28, 16, 10), items=("2.02", "9.01")))
    return out


def events():
    px = prices()
    idx = {d.date(): 1.0 for d in px["date"]}
    rows, q = P.build_events("SYNT", TF.CIK, filings(), TF.make_doc(), px, idx, regime={d.date(): 1.0 for d in px["date"]})
    return {r["period_accession"]: r for r in rows}, q


def test_the_q3_2025_event_is_an_after_close_release_reacting_the_next_session():
    ev, q = events()
    e = ev["0000000001-25-000003"]
    assert e["release_source"] == "8k_item_2_02" and e["release_accession"] == "0000000001-25-000050" and e["timing"] == "AMC"
    assert e["reaction_date"] == "2025-10-29" and e["decision_accepted_at"].startswith("2025-10-28T20:10")           # 16:10 EDT = 20:10Z
    assert abs(e["ar_react"] - 0.10) < 1e-9 and abs(e["gap"] - 0.10) < 1e-9 and abs(e["relvol"] - 3.0) < 1e-9
    assert e["nature"] == "recurring_operating" and "revenue_acceleration" in e["anomaly_rules"]
    assert abs(e["ar_d5"]) < 1e-9 and abs(e["ar_d20"]) < 1e-9                                                           # the drift starts AFTER the reaction bar: flat afterwards
    assert abs(e["prev_gap"]) < 1e-9 and abs(e["prev_ar_react"]) < 1e-9                                           # placebo: nothing happens on the bar BEFORE the reaction bar


def test_a_report_without_a_matching_8k_uses_its_own_acceptance_and_says_so():
    ev, q = events()
    e = ev["0000000001-25-000002"]
    assert e["release_source"] == "periodic_filing" and e["release_accession"] is None and q["no_8k_release_matched"] >= 1


def test_the_quality_counters_describe_what_could_not_be_used():
    ev, q = events()
    assert q["periodic_filings"] == 6 and q["events"] == len(ev)
    assert q["no_prior_year_comparison"] >= 3                       # the 2024 quarters have no prior year in the data


def test_the_source_url_points_at_the_filing_archive():
    ev, _ = events()
    assert P.filing_url(TF.CIK, "0000000001-25-000050", "x.htm") == f"https://www.sec.gov/Archives/edgar/data/{TF.CIK}/000000000125000050/x.htm"
