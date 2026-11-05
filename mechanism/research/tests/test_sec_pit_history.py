"""SEC point-in-time history study: the reserved confirmation window and stale quarters are removed before any price is read, and the reconstruction summary records a clean leakage audit
and an exact match with the production run. Synthetic events plus the committed summary; no network, no database, no price data."""
import json
import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from research.sec_pit_history import study as S  # noqa: E402

HERE = os.path.join(ROOT, "mechanism", "research", "sec_pit_history")


def _ev(rows):
    return pd.DataFrame(rows, columns=["symbol", "quarter_end", "available_at", "event_type"]).assign(
        quarter_end=lambda d: pd.to_datetime(d["quarter_end"]), available_at=lambda d: pd.to_datetime(d["available_at"]))


def test_holdout_and_stale_quarters_never_reach_the_outcome_stage():
    ev = _ev([("AAA", "2022-09-30", "2022-11-01", "margin_expansion"),       # pre-holdout: kept
              ("AAA", "2022-09-30", "2022-11-01", "revenue_acceleration"),
              ("BBB", "2022-12-31", "2023-01-01", "margin_expansion"),       # first holdout day
              ("BBB", "2025-03-01", "2025-03-31", "fcf_turnaround"),         # last holdout day
              ("CCC", "2025-03-01", "2025-04-01", "fcf_turnaround"),         # first day after: kept
              ("DDD", "2019-03-31", "2020-06-01", "margin_expansion")])      # first public 428 days after quarter end: stale
    out, n_hold, n_hold_cq, n_stale = S.exploration_events(ev)
    assert (n_hold, n_hold_cq, n_stale) == (2, 2, 1)
    assert sorted(out["symbol"]) == ["AAA", "AAA", "CCC"]
    assert not out["available_at"].between(S.HOLDOUT[0], S.HOLDOUT[1] - pd.Timedelta(days=1)).any()


def test_the_holdout_is_the_registered_confirmation_window():
    assert (S.HOLDOUT[0], S.HOLDOUT[1]) == (pd.Timestamp("2023-01-01"), pd.Timestamp("2025-04-01"))


def test_the_committed_reconstruction_is_leak_free_and_reproduces_production():
    r = json.load(open(os.path.join(HERE, "reconstruction.json"), encoding="utf-8"))
    assert r["leakage_violations"] == 0 and r["method_version"] == "inflection_v2_sec_pit"
    pc = r["production_check"]
    assert pc["identical"] == pc["production_base_events"] == pc["offline_base_events_in_window"] and pc["n_only_production"] == pc["n_only_offline"] == 0


def test_survivorship_index_parser_and_reason_classifier():
    from datetime import date
    from research.sec_pit_history import survivorship_fetch as SF
    idx = ("Form Type   Company Name                                                  CIK         Date Filed  File Name\n"
           "---------------------------------------------------------------------------------------------------------------\n"
           "10-Q        ACME CORP                                                     1234567     2019-05-10  edgar/data/1234567/x.txt\n"
           "25-NSE      ACME CORP                                                     1234567     2019-09-03  edgar/data/1234567/y.txt\n")
    rows = list(SF.parse_form_index(idx))
    assert rows == [("10-Q", "ACME CORP", 1234567, date(2019, 5, 10)), ("25-NSE", "ACME CORP", 1234567, date(2019, 9, 3))]
    d = date(2019, 9, 3)
    rec = lambda forms, dates, items: {"form": forms, "filingDate": dates, "items": items}  # noqa: E731
    assert SF.classify(rec(["8-K"], ["2019-08-20"], ["1.03,7.01"]), d)[0] == "bankruptcy"
    assert SF.classify(rec(["DEFM14A", "8-K"], ["2019-06-01", "2019-08-30"], ["", "2.01,5.01"]), d)[0] == "merger"
    assert SF.classify(rec(["8-K"], ["2019-07-01"], ["3.01"]), d)[0] == "listing_failure"
    assert SF.classify(rec(["8-K"], ["2018-01-01"], ["1.03"]), d)[0] == "unknown"          # outside the window


def test_survivorship_bounds_arithmetic_on_a_synthetic_case():
    from research.sec_pit_history import survivorship_bounds as SB
    t0 = pd.Timestamp("2019-01-31")
    rows = pd.DataFrame({"available_at": [t0] * 100, "n_types": [2] * 50 + [1] * 50, "hlv": [1.0] * 4 + [0.0] * 96, "hlv_exp": [0.06] * 100})
    pool = pd.DataFrame({"t": [t0] * 1000, "hlv": [1.0] * 50 + [0.0] * 950})                      # base rate 5%
    cands = pd.DataFrame({"cik": [1, 2], "delist_date": [t0 + pd.Timedelta(days=200)] * 2, "reason": ["bankruptcy", "merger"], "excluded": ["", ""],
                          "first_periodic": [t0 - pd.Timedelta(days=900)] * 2})
    events = pd.DataFrame({"cik": [1, 1], "quarter_end": [t0 - pd.Timedelta(days=40)] * 2, "event_type": ["margin_expansion", "revenue_acceleration"],
                           "available_at": [t0 + pd.Timedelta(days=5)] * 2})
    res = SB.compute(rows, pool, cands, events, pd.DataFrame(columns=["cik", "end", "filed", "value"]))
    r = res[(res["eligibility"] == "any") & (res["cut"] == "2+ event types")].set_index("scenario")
    assert r.loc["S1 mild (-30%, not halved)", "missing events (imputed)"] == 1
    assert r.loc["S1 mild (-30%, not halved)", "corrected halved"] == f"{4 / 51 * 100:.2f}%"               # the bankrupt company's event is not halved at -30%
    assert r.loc["S3 severe (-100%, halved)", "corrected halved"] == f"{5 / 51 * 100:.2f}%"
    # controls: 2 missing company-months; S2 halves the bankrupt one, the merger never -> pool rate (50+1)/1002
    assert r.loc["S2 Shumway (-55%, halved)", "corrected expected"] == f"{0.06 * ((50 + 1) / 1002) / 0.05 * 100:.2f}%"
    # S4 halves the event side only
    assert r.loc["S4 adversarial (events -100%, controls -30%)", "corrected expected"] == f"{0.06 * (50 / 1002) / 0.05 * 100:.2f}%"
