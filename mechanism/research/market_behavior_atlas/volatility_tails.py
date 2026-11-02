"""Addendum to the Atlas (§6): inside the high-volatility group, does anything observable at the start separate future doublers (>= +100% in 250
sessions) from future halvers (<= -50%)? Descriptive, on the already-examined 2017-2026 history: hypothesis-generating only.

    cd mechanism/research/market_behavior_atlas && python volatility_tails.py > volatility_tails.txt

Design (fixed before running):
  * high volatility = top within-date quintile of 60-day volatility (eligible stocks, month-end rows with a 250-session outcome)
  * each feature is split into within-date terciles computed INSIDE that quintile (so volatility itself is held roughly constant)
  * reported: doubling rate, halving rate, and their ratio (the "skew" of outcomes), month-equal; 2020 start months shown separately because they
    dominate the right tail; with and without 2020
  * then a stratified comparison: the doubling-to-halving ratio by volatility decile x size (dollar-volume) tercile x trend regime, so that
    the reader can see whether a feature effect survives within cells
"""
import numpy as np
import pandas as pd

import atlas_lib as A

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

FEATS = {"ret126": "6-month return", "ret21": "1-month return", "dist_52w_high": "distance from 52w high", "adv20_musd": "dollar volume (size)",
         "volume_trend": "volume trend 20d/120d", "new55_high_last20": "new 55d high in last 20d", "price": "share price"}


def rates(df):
    g = df.groupby("date")
    w, l = g["r250"].apply(lambda s: (s >= 1).mean()).mean(), g["r250"].apply(lambda s: (s <= -.5).mean()).mean()
    return {"stock-months": len(df), "months": df["date"].nunique(), "doubled": round(w, 4), "halved": round(l, 4), "doubled/halved": round(w / l, 2) if l > 0 else None,
            "median 250d": round(float(g["r250"].median().mean()), 4)}


def main():
    p = A.load_panel("data/prices")
    reg = A.build_regimes(p)
    fp = A.attach(A.forward_panel(p), reg, None).dropna(subset=["r250", "vol60_pct"])
    hv = fp[fp["vol60_pct"] > 0.8].copy()
    print(f"High-volatility group: {len(hv):,} stock-months, {hv['symbol'].nunique()} stocks, {hv['date'].nunique()} months; rest of universe for reference:")
    print(pd.DataFrame([{"group": "top vol quintile", **rates(hv)}, {"group": "other 80%", **rates(fp[fp['vol60_pct'] <= 0.8])},
                        {"group": "top vol quintile, ex-2020", **rates(hv[hv['date'].dt.year != 2020])}]).to_string(index=False))
    rows = []
    for f, label in FEATS.items():
        x = hv.dropna(subset=[f]).copy()
        if f == "new55_high_last20":
            x["bucket"] = np.where(x[f] > 0, "yes", "no")
        else:
            x["bucket"] = pd.cut(x.groupby("date")[f].rank(pct=True), [0, 1 / 3, 2 / 3, 1], labels=["low", "mid", "high"], include_lowest=True)
        for b, sub in x.groupby("bucket"):
            for era, s in (("all years", sub), ("ex-2020", sub[sub["date"].dt.year != 2020])):
                rows.append({"feature": label, "tercile (within the quintile)": b, "era": era, **rates(s)})
    print("\n=== Inside the top volatility quintile: outcome by within-date tercile of each feature ===")
    print(pd.DataFrame(rows).to_string(index=False))
    # stratified view: doubling/halving by vol decile x size tercile x trend
    fp["vol_dec"] = np.minimum((fp["vol60_pct"] * 10).astype(int), 9) + 1
    fp["size_t"] = pd.cut(fp["adv20_musd_pct"], [0, 1 / 3, 2 / 3, 1], labels=["small", "mid", "large"], include_lowest=True)
    top = fp[fp["vol_dec"] >= 8]
    cells = []
    for (vd, st, tr), s in top.groupby(["vol_dec", "size_t", "trend"]):
        if len(s) >= 400:
            cells.append({"vol decile": vd, "size tercile": st, "trend": tr, **rates(s)})
    print("\n=== Stratified (volatility decile 8-10 x size tercile x trend regime; cells with >= 400 stock-months) ===")
    print(pd.DataFrame(cells).to_string(index=False))


if __name__ == "__main__":
    main()
