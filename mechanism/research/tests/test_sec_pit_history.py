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
