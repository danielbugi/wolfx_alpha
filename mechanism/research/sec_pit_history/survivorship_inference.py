"""Survivorship bounds: uncertainty, independence and economics (owner request 2026-11-06). Pre-holdout only; the holdout stays closed.

    python mechanism/research/sec_pit_history/survivorship_inference.py > mechanism/research/sec_pit_history/survivorship_inference.txt

Same inputs and scenarios as survivorship_bounds.py (whose point estimates this reproduces). Added here:
  * 95% intervals for every scenario x eligibility x cut, by three bootstraps (2,000 draws, seed 7):
      company   resample companies (observed and missing separately), imputed outcomes drawn as Bernoulli(p) in every draw;
      quarter   resample calendar quarters of the public date (time clusters: the 250-session windows of one quarter overlap);
      both      resample companies, then keep only draws' rows from resampled quarters (two-way, conservative);
  * independent companies: one observation per company (its mean difference), so frequent filers do not dominate;
  * independent time: per-year differences (250-session windows of different calendar years barely overlap) and their sign consistency;
  * economics: what the smallest difference is worth as a loss filter, against trading and opportunity costs.
Nothing here is evidence of alpha: the difference concerns a left-tail (halving) rate, outcomes of missing companies are imputed, and the holdout is untouched.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import survivorship_bounds as SB  # noqa: E402

RNG_SEED, DRAWS = 7, 2000


def load():
    rows = pd.read_csv(os.path.join(SB.OUT, "study_rows.csv.gz"), parse_dates=["available_at", "entry"])
    pool = pd.read_csv(os.path.join(SB.OUT, "reverse_view_rows.csv.gz"), parse_dates=["t"])
    cands = pd.read_csv(os.path.join(SB.DL, "candidates.csv"), parse_dates=["delist_date", "first_periodic", "last_periodic"])
    events = pd.read_csv(os.path.join(SB.DL, "events.csv"), parse_dates=["available_at", "quarter_end"])
    floats = pd.read_csv(os.path.join(SB.DL, "floats.csv"), parse_dates=["end", "filed"])
    rows = rows[rows["available_at"] < SB.HOLDOUT_START].dropna(subset=["hlv"]).copy()
    pool = pool[pool["t"] < SB.HOLDOUT_START]
    return rows, pool, cands, events, floats


def missing_sets(rows, pool, cands, events, floats):
    """Per eligibility variant: the missing company-quarters (with cik, date, delisting) and the missing stock-months, as survivorship_bounds builds them."""
    base = float(pool["hlv"].mean())
    snaps = sorted(pool["t"].unique())
    cands = cands[cands["excluded"].fillna("") == ""].copy()
    ev = events.merge(cands[["cik", "delist_date", "reason"]], on="cik")
    ev = ev[(ev["available_at"] >= SB.WINDOW_START) & (ev["available_at"] < SB.HOLDOUT_START) & (ev["available_at"] < ev["delist_date"])]
    cq = ev.groupby(["cik", "quarter_end"]).agg(available_at=("available_at", "min"), n_types=("event_type", "nunique"), delist=("delist_date", "first"),
                                                 reason=("reason", "first")).reset_index()
    out = {}
    for variant in ("any", "float>=75M"):
        cq_v = cq[[SB.eligible(r.cik, r.available_at, floats, variant) for r in cq.itertuples()]].copy()
        months = [(r.cik, t, r.delist_date, r.reason) for r in cands.itertuples() for t in snaps
                  if r.first_periodic <= t < r.delist_date and SB.eligible(r.cik, t, floats, variant)]
        out[variant] = (cq_v, months)
    return base, out


def quarter_key(s: pd.Series) -> pd.Series:
    return s.dt.year.astype(str) + "Q" + s.dt.quarter.astype(str)


def boot(obs: pd.DataFrame, mis: pd.DataFrame, k: float, mode: str, rng) -> np.ndarray:
    """Corrected difference per draw. obs: hlv, hlv_exp, cid, q. mis: p (imputed halving probability), cid, q. k = pool rate' / pool rate."""
    o_by = {c: g.index.values for c, g in obs.groupby("cid")}
    m_by = {c: g.index.values for c, g in mis.groupby("cid")}
    qs = sorted(set(obs["q"]) | set(mis["q"]))
    oc, mc = np.array(list(o_by)), np.array(list(m_by))
    hlv, exp_, p = obs["hlv"].to_numpy(), obs["hlv_exp"].to_numpy(), mis["p"].to_numpy()
    oq, mq = obs["q"].to_numpy(), mis["q"].to_numpy()
    res = np.empty(DRAWS)
    for b in range(DRAWS):
        if mode in ("company", "both"):
            oi = np.concatenate([o_by[c] for c in rng.choice(oc, len(oc))]) if len(oc) else np.array([], int)
            mi = np.concatenate([m_by[c] for c in rng.choice(mc, len(mc))]) if len(mc) else np.array([], int)
        else:
            oi, mi = np.arange(len(obs)), np.arange(len(mis))
        if mode in ("quarter", "both"):
            w = pd.Series(rng.choice(qs, len(qs))).value_counts()           # quarter -> times drawn
            ow = pd.Series(oq[oi]).map(w).fillna(0).to_numpy()
            mw = pd.Series(mq[mi]).map(w).fillna(0).to_numpy()
        else:
            ow, mw = np.ones(len(oi)), np.ones(len(mi))
        draws = rng.random(len(mi)) < p[mi]
        n = ow.sum() + mw.sum()
        ev_rate = ((hlv[oi] * ow).sum() + (draws * mw).sum()) / n
        res[b] = ev_rate - (exp_[oi] * ow).sum() / max(ow.sum(), 1e-12) * k
    return res


def ci(x):
    return f"[{np.percentile(x, 2.5) * 100:+.2f}, {np.percentile(x, 97.5) * 100:+.2f}] pp; P(<0) {np.mean(x < 0):.3f}"


def main() -> int:
    rows, pool, cands, events, floats = load()
    base, sets = missing_sets(rows, pool, cands, events, floats)
    rows = rows.assign(cid="s:" + rows["symbol"], q=quarter_key(rows["available_at"]), d=rows["hlv"] - rows["hlv_exp"])
    rng = np.random.default_rng(RNG_SEED)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)

    print("Pre-holdout only (public before 2023-01-01); holdout not opened. pp = percentage points of the 250-session halving rate (event minus matched expectation).\n")
    print("=== 1. Uncertainty of every scenario (corrected difference; 95% bootstrap intervals; imputed outcomes drawn, not fixed) ===")
    out = []
    for variant, (cq_v, months) in sets.items():
        for name, scen in SB.SCENARIOS.items():
            pc = sum(SB.halved_prob(reason, d, t, "control", scen, base) for _, t, d, reason in months)
            k = ((base * len(pool) + pc) / (len(pool) + len(months))) / base
            for cut, nmin in (("2+ event types", 2), ("all company-quarters", 1)):
                o = rows[rows["n_types"] >= nmin].reset_index(drop=True)
                m = cq_v[cq_v["n_types"] >= nmin].reset_index(drop=True)
                m = m.assign(p=[SB.halved_prob(r.reason, r.delist, r.available_at, "event", scen, base) for r in m.itertuples()],
                             cid="c:" + m["cik"].astype(str), q=quarter_key(m["available_at"]))
                point = (o["hlv"].sum() + m["p"].sum()) / (len(o) + len(m)) - o["hlv_exp"].mean() * k
                row = {"eligibility": variant, "scenario": name.split(" (")[0], "cut": cut, "point": f"{point * 100:+.2f} pp"}
                for mode in ("company", "quarter", "both"):
                    row[f"{mode} CI"] = ci(boot(o, m, k, mode, rng))
                out.append(row)
    print(pd.DataFrame(out).to_string(index=False))

    print("\n=== 2. Observed (survivor-only) difference, 2+ event types: independence checks ===")
    o = rows[rows["n_types"] >= 2]
    per_co = o.groupby("cid")["d"].mean()
    bs = [rng.choice(per_co.to_numpy(), len(per_co)).mean() for _ in range(DRAWS)]
    print(f"company-quarters {len(o):,}; companies {o['cid'].nunique():,}; calendar quarters {o['q'].nunique()}")
    print(f"pooled (every company-quarter)          {o['d'].mean() * 100:+.2f} pp")
    print(f"one observation per company (mean of d)  {per_co.mean() * 100:+.2f} pp  95% CI [{np.percentile(bs, 2.5) * 100:+.2f}, {np.percentile(bs, 97.5) * 100:+.2f}]; "
          f"companies with d<0: {(per_co < 0).mean() * 100:.0f}%, d>0: {(per_co > 0).mean() * 100:.0f}%")
    first = o.sort_values("available_at").groupby("cid").head(1)
    print(f"first qualifying quarter per company     {first['d'].mean() * 100:+.2f} pp (n={len(first):,})")
    yr = o.groupby(o["available_at"].dt.year).agg(n=("d", "size"), companies=("cid", "nunique"), halved=("hlv", "mean"), expected=("hlv_exp", "mean"), diff=("d", "mean"))
    yr[["halved", "expected", "diff"]] = (yr[["halved", "expected", "diff"]] * 100).round(2)
    print("per calendar year of the public date (pp):\n" + yr.to_string())
    print(f"years with a negative difference: {(yr['diff'] < 0).sum()} of {len(yr)}")
    qd = o.groupby("q")["d"].mean()
    print(f"per calendar quarter: {len(qd)} quarters, negative in {(qd < 0).sum()}; mean of quarter means {qd.mean() * 100:+.2f} pp")

    print("\n=== 3. Economics of the smallest robust effect (stacked events, adversarial scenario) ===")
    halved_r = pool.loc[pool["r"] <= -0.5, "r"]
    loss = float(-halved_r.mean())
    x = o["x250"].dropna()
    xb = [rng.choice(x.to_numpy(), len(x)).mean() for _ in range(DRAWS)]
    for label, dpp in (("adversarial, any eligibility", 0.0027), ("adversarial, float >= $75M", 0.0071), ("observed survivor-only", 0.0229)):
        print(f"{label:32s} halving rate lower by {dpp * 100:.2f} pp  x  mean loss when halved {loss * 100:.0f}%  =  {dpp * loss * 100:.2f}% of position value per 250 sessions")
    print(f"for scale: observed 250-session excess return of stacked events vs matched stocks {x.mean() * 100:+.2f}% "
          f"(event-level bootstrap [{np.percentile(xb, 2.5) * 100:+.2f}, {np.percentile(xb, 97.5) * 100:+.2f}]; survivor-biased upward)")
    print("costs: a round trip in this universe (close >= $5, >= $2M daily dollar volume) costs about 0.10-0.60% (spread + impact + commission; small names at the top);")
    print("an annual rebalance pays it once, a quarterly one up to four times; the opportunity cost is the excluded names' return (the filter removes candidates, it does not add any).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
