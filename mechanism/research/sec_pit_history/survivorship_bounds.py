"""Survivorship bounds (SURVIVORSHIP_BOUNDS_SPEC.md): add the missing (delisted) companies to the exploratory pre-holdout halving comparison under
fixed scenarios, keeping OBSERVED counts and IMPUTED outcomes apart. Read-only; holdout closed (events public from 2023-01-01 are not used).

    python mechanism/research/sec_pit_history/survivorship_bounds.py > mechanism/research/sec_pit_history/survivorship_bounds.txt

Inputs: data/out/study_rows.csv.gz and reverse_view_rows.csv.gz (study.py), data/delist/{candidates,events,floats}.csv (survivorship_fetch.py).

Method (pooled rates; the observed study's matched expectation is scaled by the change in the pool halving rate the missing companies cause):
  event rate'    = (observed halved events + imputed halved missing events) / (observed events + missing events)
  pool rate'     = (observed pool halving rate x pool stock-months + imputed halved missing stock-months) / (pool + missing stock-months)
  expected'      = observed matched expectation x pool rate' / observed pool rate
  difference'    = event rate' - expected'
A missing company-quarter / company-month at date t: if the company delisted within 365 days after t, its halving is imputed from the delisting
reason and the scenario; otherwise it survived the window and gets the observed pool base rate (doubled for events in S4).
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from typing import Dict, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT, DL = os.path.join(HERE, "data", "out"), os.path.join(HERE, "data", "delist")
HOLDOUT_START = pd.Timestamp("2023-01-01")
WINDOW_START = pd.Timestamp("2017-09-01")
PERF = {"bankruptcy", "listing_failure", "deregistration"}

# scenario -> (performance-delisting halved probability for events, for controls, unknown-reason treated as performance?, survived-window multiplier for events)
SCENARIOS: Dict[str, Tuple[float, float, bool, float]] = {
    "S1 mild (-30%, not halved)": (0.0, 0.0, False, 1.0),
    "S2 Shumway (-55%, halved)": (1.0, 1.0, True, 1.0),
    "S3 severe (-100%, halved)": (1.0, 1.0, True, 1.0),
    "S4 adversarial (events -100%, controls -30%)": (1.0, 0.0, True, 2.0),
}


def halved_prob(reason: str, delist: pd.Timestamp, t: pd.Timestamp, side: str, scen: Tuple[float, float, bool, float], base: float) -> float:
    p_ev, p_ctl, unknown_is_perf, surv_mult = scen
    if delist - t <= pd.Timedelta(days=365):
        perf = reason in PERF or (reason == "unknown" and (unknown_is_perf if side == "event" else (unknown_is_perf and p_ctl > 0)))
        if not perf:
            return 0.0                                  # merger (or unknown treated as merger): a deal price is not a halving
        return p_ev if side == "event" else p_ctl
    return min(1.0, base * (surv_mult if side == "event" else 1.0))


def eligible(cik: int, t: pd.Timestamp, floats: pd.DataFrame, variant: str) -> bool:
    if variant == "any":
        return True
    f = floats[(floats["cik"] == cik) & (floats["filed"] <= t)]
    return bool(len(f)) and float(f.sort_values("filed")["value"].iloc[-1]) >= 75e6


def compute(rows: pd.DataFrame, pool: pd.DataFrame, cands: pd.DataFrame, events: pd.DataFrame, floats: pd.DataFrame) -> pd.DataFrame:
    rows = rows[(rows["available_at"] < HOLDOUT_START)].dropna(subset=["hlv"])
    pool = pool[pool["t"] < HOLDOUT_START]
    base = float(pool["hlv"].mean())
    snaps = sorted(pool["t"].unique())
    cands = cands[cands["excluded"].fillna("") == ""].copy()
    ev = events.merge(cands[["cik", "delist_date", "reason"]], on="cik")
    ev = ev[(ev["available_at"] >= WINDOW_START) & (ev["available_at"] < HOLDOUT_START) & (ev["available_at"] < ev["delist_date"])]
    cq = ev.groupby(["cik", "quarter_end"]).agg(available_at=("available_at", "min"), n_types=("event_type", "nunique"), delist=("delist_date", "first"),
                                                 reason=("reason", "first")).reset_index()
    out = []
    for variant in ("any", "float>=75M"):
        cq_v = cq[[eligible(r.cik, r.available_at, floats, variant) for r in cq.itertuples()]]
        months = [(r.cik, t, r.delist_date, r.reason) for r in cands.itertuples() for t in snaps
                  if r.first_periodic <= t < r.delist_date and eligible(r.cik, t, floats, variant)]
        for name, scen in SCENARIOS.items():
            pc = sum(halved_prob(reason, d, t, "control", scen, base) for _, t, d, reason in months)
            pool_rate = (base * len(pool) + pc) / (len(pool) + len(months))
            for cut, sel_obs, sel_mis in (("all company-quarters", rows["n_types"] >= 1, cq_v["n_types"] >= 1), ("2+ event types", rows["n_types"] >= 2, cq_v["n_types"] >= 2)):
                o, m = rows[sel_obs], cq_v[sel_mis]
                pe = sum(halved_prob(r.reason, r.delist, r.available_at, "event", scen, base) for r in m.itertuples())
                ev_rate = (o["hlv"].sum() + pe) / (len(o) + len(m))
                exp_obs = float(o["hlv_exp"].mean())
                exp_new = exp_obs * pool_rate / base
                out.append({"eligibility": variant, "scenario": name, "cut": cut, "observed events": len(o), "missing events (imputed)": len(m),
                            "observed halved": f"{o['hlv'].mean() * 100:.2f}%", "observed expected": f"{exp_obs * 100:.2f}%",
                            "observed diff": f"{(o['hlv'].mean() - exp_obs) * 100:+.2f} pp",
                            "pool stock-months": len(pool), "missing stock-months (imputed)": len(months),
                            "corrected halved": f"{ev_rate * 100:.2f}%", "corrected expected": f"{exp_new * 100:.2f}%",
                            "corrected diff": f"{(ev_rate - exp_new) * 100:+.2f} pp", "_diff": ev_rate - exp_new})
    return pd.DataFrame(out)


def main() -> int:
    rows = pd.read_csv(os.path.join(OUT, "study_rows.csv.gz"), parse_dates=["available_at", "entry"])
    pool = pd.read_csv(os.path.join(OUT, "reverse_view_rows.csv.gz"), parse_dates=["t"])
    cands = pd.read_csv(os.path.join(DL, "candidates.csv"), parse_dates=["delist_date", "first_periodic", "last_periodic"])
    events = pd.read_csv(os.path.join(DL, "events.csv"), parse_dates=["available_at", "quarter_end"])
    floats = pd.read_csv(os.path.join(DL, "floats.csv"), parse_dates=["end", "filed"]) if os.path.getsize(os.path.join(DL, "floats.csv")) else pd.DataFrame(columns=["cik", "end", "filed", "value"])
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    ok = cands[cands["excluded"].fillna("") == ""]
    print(f"Missing (delisted, not in our universe) companies: {len(cands):,} candidates, {len(ok):,} after exclusions (blank-check / parse errors).")
    print("By delisting reason (OBSERVED from SEC filings):")
    print(ok["reason"].value_counts().to_string())
    print(f"\nTheir point-in-time inflection events (OBSERVED, same engine, 120-day stale guard): {len(events):,}; pre-holdout window: "
          f"{int(((events['available_at'] >= WINDOW_START) & (events['available_at'] < HOLDOUT_START)).sum()):,}\n")
    res = compute(rows, pool, cands, events, floats)
    print("=== Bounds: observed vs corrected halving comparison (outcomes of missing companies are IMPUTED per scenario) ===")
    print(res.drop(columns="_diff").to_string(index=False))
    st = res[res["cut"] == "2+ event types"]
    robust = all(st[st["scenario"].str.startswith(s)]["_diff"].max() < 0 for s in ("S1", "S2", "S3")) and (st[st["scenario"].str.startswith("S4")]["_diff"] < 0).all()
    print(f"\nDecision rule (fixed in the spec): {'ROBUST to survivorship' if robust else 'SURVIVORSHIP-SENSITIVE'} for the stacked-event difference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
