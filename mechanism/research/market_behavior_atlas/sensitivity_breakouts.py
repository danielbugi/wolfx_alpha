"""Addendum to the Atlas (§5): are the breakout-failure and breakdown-reversal rates sensitive to the event definition and to overlapping events?

    cd mechanism/research/market_behavior_atlas && python sensitivity_breakouts.py > sensitivity_breakouts.txt

Variants (all decided before running; nothing is selected from them):
  * failure window 5 / 10 / 20 sessions, and a tolerance: "failed" = a close more than 0% / 2% / 5% below the breakout level (above, for breakdowns)
  * all events vs INDEPENDENT events (the first event of a stock, then nothing from that stock for 60 sessions)
  * pooled rates vs month-equal rates with a 3-month moving-block bootstrap interval
  * excess return vs the equal-weight universe (as in §5) vs a VOLATILITY-MATCHED baseline: the same-date mean of stocks in the same
    within-date decile of 60-day volatility (a breakdown is a volatile stock in a falling tape; the universe is the wrong comparison)
"""
import numpy as np
import pandas as pd

import atlas_lib as A

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)


def fwd_extreme(C: pd.DataFrame, w: int, kind: str) -> np.ndarray:
    """min (breakouts) / max (breakdowns) of closes in (t, t+w]."""
    r = C[::-1].rolling(w, min_periods=1)
    x = (r.min() if kind == "up" else r.max())[::-1].shift(-1)
    return x.values


def independent(ev: pd.DataFrame, idx: pd.DatetimeIndex, gap: int = 60) -> pd.Series:
    pos = idx.get_indexer(ev["date"])
    keep = np.zeros(len(ev), dtype=bool)
    last = {}
    for k in np.argsort(pos, kind="stable"):
        s = ev["symbol"].iat[k]
        if s not in last or pos[k] - last[s] > gap:
            keep[k] = True
            last[s] = pos[k]
    return pd.Series(keep, index=ev.index)


def month_rate(flag: pd.Series, dates: pd.Series):
    m = flag.groupby(dates.dt.to_period("M")).mean()
    mean, lo, hi = A.block_bootstrap(m, 3)
    return mean, lo, hi, len(m)


def main():
    p = A.load_panel("data/prices")
    reg = A.build_regimes(p)
    C, idx = p.C, p.C.index
    ewi = reg["ew_index"]
    # volatility-matched baseline: same-date mean 60-session excess return of eligible stocks in the same within-date vol60 decile
    vol = A.point_features(p)["vol60"].where(p.eligible)
    dec = np.minimum((vol.rank(axis=1, pct=True) * 10).fillna(-1).astype(int), 9)
    x60 = (C.shift(-60) / C - 1).sub(ewi.shift(-60) / ewi - 1, axis=0).where(p.eligible & A.window_clean(p.disc, 0, 60))
    base = {}
    for k in range(10):
        base[k] = x60.where(dec == k).mean(axis=1)
    base = pd.DataFrame(base)

    rows, srows = [], []
    for n, kind in ((55, "up"), (252, "up"), (55, "down"), (252, "down")):
        ev = A.breakout_events(p, n, kind).join(reg[["trend"]], on="date")
        ii, jj = idx.get_indexer(ev["date"]), C.columns.get_indexer(ev["symbol"])
        ev["ind"] = independent(ev, idx)
        lvl = ev["level"].values
        name = f"{'52w' if n == 252 else '55d'} {'breakout' if kind == 'up' else 'breakdown'}"
        for w in (5, 10, 20):
            ext = fwd_extreme(C, w, kind)[ii, jj]
            for tol in (0.0, 0.02, 0.05):
                failed = (ext < lvl * (1 - tol)) if kind == "up" else (ext > lvl * (1 + tol))
                ok = ~np.isnan(ext)
                f = pd.Series(np.where(ok, failed, np.nan), index=ev.index)
                for sample, mask in (("all events", np.ones(len(ev), bool)), ("independent (60-session gap)", ev["ind"].values)):
                    sel = mask & ok
                    mm, lo, hi, nm = month_rate(f[sel].astype(float), ev.loc[sel, "date"])
                    rows.append({"event": name, "window": w, "tolerance": tol, "sample": sample, "events": int(sel.sum()), "pooled": round(float(f[sel].mean()), 3),
                                 "month-equal": round(mm, 3), "CI": f"[{lo:.3f}, {hi:.3f}]", "months": nm})
        # 60-session outcome: vs universe and vs the volatility-matched baseline, by trend regime, all vs independent
        d = dec.values[ii, jj]
        matched = np.where(d >= 0, base.values[ii, np.clip(d, 0, 9)], np.nan)
        ev["x60_vs_vol_matched"] = ev["x60"] - matched
        for sample, mask in (("all", np.ones(len(ev), bool)), ("independent", ev["ind"].values)):
            for g in ("Bull", "Bear", "Weakening", "Recovery"):
                sub = ev[mask & (ev["trend"] == g).values].dropna(subset=["x60", "x60_vs_vol_matched"])
                if len(sub) < 30:
                    continue
                a = A.block_bootstrap(sub.groupby(sub["date"].dt.to_period("M"))["x60_vs_vol_matched"].mean(), 3)
                srows.append({"event": name, "sample": sample, "trend": g, "events": len(sub), "months": sub["date"].dt.to_period("M").nunique(),
                              "spells": A.spells(reg["trend"].dropna(), g), "recovered60" if kind == "down" else "sustained60": round(float(sub["recovered60" if kind == "down" else "sustained60"].mean()), 3),
                              "mean x60 vs universe": round(float(sub["x60"].mean()), 4), "mean x60 vs vol-matched": round(float(sub["x60_vs_vol_matched"].mean()), 4),
                              "month-equal vs vol-matched [CI]": f"{a[0]:+.4f} [{a[1]:+.4f}, {a[2]:+.4f}]"})
    print("=== Failure rate (closed back through the level) under alternative definitions ===")
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n=== 60-session outcome by trend regime: vs the universe and vs a volatility-matched same-date baseline ===")
    print(pd.DataFrame(srows).to_string(index=False))


if __name__ == "__main__":
    main()
