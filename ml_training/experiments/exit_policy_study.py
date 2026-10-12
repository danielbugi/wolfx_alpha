"""Donchian exit-policy comparison on REAL breakout events (read-only database session; writes /out/exit_policy_study.json).

Events: ml_breakout_dataset_v2 (the repository's own Donchian-20 breakout detector, liquidity floor and discontinuity exclusion). A deterministic ~12.5 % sample of events (md5 of
symbol||date) keeps the run short; the sample is unrelated to outcomes. Each event is simulated under every policy in mechanism/research/exit_policies.py with the SAME entry (next open),
the SAME risk unit (2 x ATR14 at the signal bar), the SAME 60-bar cap and the SAME costs. Policy parameters are declared a priori and were not tuned.
"""
import json
import sys
import time
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path[:0] = ["/app", "/app/mechanism", "/exp"]
from ml_training.features import price_features as pf  # noqa: E402
from research import exit_policies as X  # noqa: E402
import _common as C  # noqa: E402

T0 = time.time()
MAX_HOLD = X.DEFAULT_MAX_HOLD
COSTS = (0.0, 10.0, 25.0)       # round-trip bps: gross, plausible liquid-name cost, conservative


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


conn = C.connect()
ev = pd.read_sql("SELECT symbol, date, direction, features FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s AND md5(symbol || date::text) ~ '^[01]' ORDER BY symbol, date", conn, params=(pf.FEATURE_SET_VERSION,))
ev["date"] = pd.to_datetime(ev["date"])
sect_cols = [f"sector_{s.lower().replace(' ', '_')}" for s in pf.SECTORS]
sect = pd.DataFrame(ev.pop("features").tolist())[sect_cols].fillna(0.0)
ev["sector"] = np.where(sect.sum(axis=1) > 0, sect.idxmax(axis=1).str.replace("sector_", ""), "unknown")
disc = pd.read_sql("SELECT symbol, date FROM price_discontinuities", conn)
disc_by = defaultdict(list)
for s_, d_ in zip(disc.symbol, pd.to_datetime(disc.date)):
    disc_by[s_].append(d_)
mk = C.market_panel(conn)
log("events", len(ev), "symbols", ev.symbol.nunique(), "market sessions", len(mk))

POL = list(X.POLICIES)
rows = []                      # one row per (event, policy)
skipped = defaultdict(int)
for si, (sym, g) in enumerate(ev.groupby("symbol")):
    px = pd.read_sql("SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s ORDER BY date", conn, params=(sym,))
    px = px.dropna(subset=["close"]).drop_duplicates("date").reset_index(drop=True)
    px["date"] = pd.to_datetime(px["date"])
    for k in ("open", "high", "low", "close", "volume"):
        px[k] = pd.to_numeric(px[k], errors="coerce")
    if len(px) < 300:
        skipped["short_history"] += len(g)
        continue
    ind = pf.compute_indicators(px)
    atr = ind["atr"].to_numpy()
    o, h, l, c = (px[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    dates = px["date"].to_numpy()
    dd = np.array(disc_by.get(sym, []), dtype="datetime64[ns]")
    for r in g.itertuples(index=False):
        i = int(np.searchsorted(dates, np.datetime64(r.date)))
        if i >= len(dates) or dates[i] != np.datetime64(r.date):
            skipped["date_not_in_prices"] += 1
            continue
        if i + MAX_HOLD + 1 >= len(dates):
            skipped["window_not_complete"] += 1
            continue
        lo_d, hi_d = dates[max(0, i - 5)], dates[min(len(dates) - 1, i + MAX_HOLD + 1)]
        if len(dd) and ((dd >= lo_d) & (dd <= hi_d)).any():
            skipped["price_discontinuity_in_window"] += 1
            continue
        win = slice(i, i + MAX_HOLD + 2)
        if not (np.isfinite(o[win]).all() and np.isfinite(h[win]).all() and np.isfinite(l[win]).all() and np.isfinite(c[win]).all()):
            skipped["nan_bars"] += 1
            continue
        for pol in POL:
            t = X.simulate(pol, o, h, l, c, i, r.direction, float(atr[i]), max_hold=MAX_HOLD, cost_bps=0.0)
            if t is None:
                skipped["not_enterable"] += 1
                continue
            unit = X.STOP_ATR * float(atr[i])
            rows.append((sym, r.date, int(r.direction), r.sector, pol, t.r_gross, t.entry_price / unit, t.duration, t.mfe_r, t.mae_r, t.reason, dates[t.exit_bar]))
    if si % 500 == 0:
        log("symbols done", si, "trades", len(rows))
log("simulated", len(rows), "skipped", dict(skipped))

T = pd.DataFrame(rows, columns=["symbol", "date", "direction", "sector", "policy", "r_gross", "px_per_r", "duration", "mfe_r", "mae_r", "reason", "exit_date"])
T["month"] = T["date"].dt.to_period("M").astype(str)
T = T.join(mk[["idx_above_200", "b50"]].reindex(T["date"]).reset_index(drop=True))
out = {"design": {"events_sampled": int(len(ev)), "events_simulated": int(T.groupby("policy").size().max()), "skipped": dict(skipped), "max_hold_bars": MAX_HOLD, "risk_unit": "2 x ATR14 at the signal bar",
                  "entry": "next open", "policies": {k: v.identity for k, v in X.POLICIES.items()}, "costs_bps_round_trip": list(COSTS),
                  "note": "parameters declared a priori; sample = md5(symbol||date) prefix 0/1 (about 12.5 % of events); survivorship: only symbols still stored; no borrow cost / dividends / halts modelled"}}


def trades_of(df, bps):
    ts = [X.Trade(p, int(d), 0, 0, 0.0, 0.0, rg, rg - (bps / 1e4) * ppr, int(du), re, mf, ma) for p, d, rg, ppr, du, re, mf, ma in
          zip(df.policy, df.direction, df.r_gross, df.px_per_r, df.duration, df.reason, df.mfe_r, df.mae_r)]
    return ts


def summarize(df, bps):
    a = X.aggregate(trades_of(df, bps), exit_dates=df["exit_date"].to_numpy())
    r = df["r_gross"].to_numpy() - (bps / 1e4) * df["px_per_r"].to_numpy()
    ci = C.block_ci(r, df["month"].to_numpy())
    a["expectancy_ci95"] = ci.get("ci95")
    if len(r):
        a["expectancy_r_winsorized_-5_to_10"] = round(float(np.clip(r, -5, 10).mean()), 4)       # outliers from very small ATR (R-normalisation) bounded
        big = np.abs(r) > 10
        a["trades_abs_r_gt_10"] = int(big.sum())
        a["total_r_from_abs_r_gt_10"] = round(float(r[big].sum()), 1)
        a["expectancy_r_excluding_abs_r_gt_10"] = round(float(r[~big].mean()), 4)
    return a


overall = {}
for pol in POL:
    d = T[T.policy == pol]
    overall[pol] = {f"cost_{int(b)}bps": summarize(d, b) for b in COSTS}
    overall[pol]["bullish_10bps"] = summarize(d[d.direction == 1], 10.0)
    overall[pol]["bearish_10bps"] = summarize(d[d.direction == -1], 10.0)
out["overall"] = overall
log("overall done")

# paired differences vs the fixed-3R policy, same events (10 bps)
base = T[T.policy == "A_atr_stop_fixed_3R"].set_index(["symbol", "date"])
paired = {}
for pol in POL:
    if pol == "A_atr_stop_fixed_3R":
        continue
    d = T[T.policy == pol].set_index(["symbol", "date"])
    j = d[["r_gross", "px_per_r", "month", "direction"]].join(base[["r_gross"]], rsuffix="_base", how="inner")
    diff = (j["r_gross"] - j["r_gross_base"]).to_numpy()
    paired[pol] = C.block_ci(diff, j["month"].to_numpy())
    paired[pol]["bullish"] = C.block_ci(diff[j.direction.to_numpy() == 1], j["month"].to_numpy()[j.direction.to_numpy() == 1])
    paired[pol]["bearish"] = C.block_ci(diff[j.direction.to_numpy() == -1], j["month"].to_numpy()[j.direction.to_numpy() == -1])
out["paired_vs_A_3R_gross_r"] = paired

# by year and by market regime (10 bps)
by_year, by_regime, by_sector = {}, {}, {}
for pol in POL:
    d = T[T.policy == pol].copy()
    d["r10"] = d["r_gross"] - 0.001 * d["px_per_r"]
    by_year[pol] = {int(y): {"bullish": round(float(g[g.direction == 1].r10.mean()), 4), "bearish": round(float(g[g.direction == -1].r10.mean()), 4), "n": int(len(g))} for y, g in d.groupby(d.date.dt.year)}
    by_regime[pol] = {}
    for dn, nm in ((1, "bullish"), (-1, "bearish")):
        gg = d[d.direction == dn]
        by_regime[pol][nm] = {"index_uptrend": {"n": int((gg.idx_above_200 == 1).sum()), "mean_r": round(float(gg[gg.idx_above_200 == 1].r10.mean()), 4)},
                              "index_downtrend": {"n": int((gg.idx_above_200 == 0).sum()), "mean_r": round(float(gg[gg.idx_above_200 == 0].r10.mean()), 4)},
                              "breadth50_ge_0.5": {"n": int((gg.b50 >= 0.5).sum()), "mean_r": round(float(gg[gg.b50 >= 0.5].r10.mean()), 4)},
                              "breadth50_lt_0.5": {"n": int((gg.b50 < 0.5).sum()), "mean_r": round(float(gg[gg.b50 < 0.5].r10.mean()), 4)}}
    by_sector[pol] = {s: {"n": int(len(g)), "mean_r": round(float(g.r10.mean()), 4)} for s, g in d.groupby("sector") if len(g) >= 500}
out["by_year_10bps"], out["by_regime_10bps"], out["by_sector_10bps"] = by_year, by_regime, by_sector
json.dump(out, open("/out/exit_policy_study.json", "w"), indent=1, default=str)
log("WROTE exit_policy_study.json")
