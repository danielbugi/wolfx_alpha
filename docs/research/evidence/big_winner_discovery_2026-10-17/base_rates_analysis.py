# ---- analysis part (price_features.py is prepended by the caller, so `find_discontinuities`, `compute_indicators`, `contaminated_mask` exist) ----
import json, os, sys, time
from collections import defaultdict
import numpy as np
import pandas as pd
import psycopg2

t0 = time.time()
conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                        options="-c default_transaction_read_only=on -c statement_timeout=1800000")
cur = conn.cursor()
cur.execute("SELECT symbol, date, direction FROM ml_breakout_dataset_v2")
sig = defaultdict(list)
for s, d, dr in cur.fetchall():
    sig[s].append((pd.Timestamp(d), int(dr)))
print("signals:", sum(len(v) for v in sig.values()), "symbols:", len(sig), flush=True)
cur.execute("SELECT symbol, min(date) FROM stock_prices GROUP BY 1")
first_year = {s: d.year for s, d in cur.fetchall()}

HZ = (20, 60, 120)
rows = []
sc = conn.cursor(name="px")
sc.itersize = 200000
sc.execute("SELECT symbol, date, open, high, low, close, volume FROM stock_prices ORDER BY symbol, date")


def process(sym, recs):
    px = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        px[c] = pd.to_numeric(px[c], errors="coerce")
    px["date"] = pd.to_datetime(px["date"])
    px = px.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(px) < 80 or sym not in sig:
        return
    ind = compute_indicators(px)
    disc = find_discontinuities(px)
    n = len(px)
    bad = {H: contaminated_mask(n, disc["pos"], forward=H) for H in HZ}
    idx_of = {d: i for i, d in enumerate(px["date"])}
    H_, L_, O_, C_ = (px[k].to_numpy(float) for k in ("high", "low", "open", "close"))
    atr = ind["atr"].to_numpy()
    dv20 = ind["dollar_vol_20"].to_numpy()
    fmax = {H: pd.Series(H_).rolling(H).max().shift(-H).to_numpy() for H in HZ}      # max high of bars i+1 .. i+H
    fmin = {H: pd.Series(L_).rolling(H).min().shift(-H).to_numpy() for H in HZ}
    for d, dr in sig[sym]:
        i = idx_of.get(d)
        if i is None or i + 1 >= n:
            continue
        a, e = atr[i], O_[i + 1]
        if not (np.isfinite(a) and a > 0 and e > 0):
            continue
        rec = {"sym": sym, "date": d, "dir": dr, "atr_pct": a / C_[i] * 100, "dv20": dv20[i], "first_year": first_year.get(sym), "i": i}
        for H in HZ:
            ok = i + H < n and not bad[H][i]
            if ok:
                fav = (fmax[H][i] - e) if dr > 0 else (e - fmin[H][i])
                rec[f"r{H}"] = fav / (2 * a)
                rec[f"p{H}"] = fav / e * 100
                rec[f"t{H}"] = dr * (C_[i + H] - e) / (2 * a)          # terminal close-to-entry move in R (realised-at-horizon, no exit rule)
            else:
                rec[f"r{H}"] = rec[f"p{H}"] = rec[f"t{H}"] = np.nan
        rows.append(rec)


cur_sym, buf = None, []
for sym, d, o, h, l, c, v in sc:
    if sym != cur_sym:
        if cur_sym is not None:
            process(cur_sym, buf)
        cur_sym, buf = sym, []
    buf.append((d, o, h, l, c, v))
process(cur_sym, buf)
df = pd.DataFrame(rows)
df["year"] = df["date"].dt.year
df["cohort"] = np.where(df["first_year"] <= 2016, "listed_by_2016", "listed_later")
print("rows with computable entry:", len(df), "elapsed %.0fs" % (time.time() - t0), flush=True)

out = {"n_signals_with_entry": int(len(df))}
v120 = df[df["r120"].notna()]
out["n_valid_120"] = int(len(v120))
out["n_valid_20_60_120"] = {str(H): int(df[f"r{H}"].notna().sum()) for H in HZ}


def rates(d, col="r120"):
    x = d[col].dropna()
    return {"n": int(len(x)), "ge3R": round(float((x >= 3).mean()), 4), "ge5R": round(float((x >= 5).mean()), 4), "ge10R": round(float((x >= 10).mean()), 4)} if len(x) else {"n": 0}


out["overall_120"] = {"all": rates(v120), "bull": rates(v120[v120.dir == 1]), "bear": rates(v120[v120.dir == -1])}
out["by_horizon_5R_bull"] = {str(H): rates(df[df.dir == 1], f"r{H}") for H in HZ}
out["by_horizon_5R_bear"] = {str(H): rates(df[df.dir == -1], f"r{H}") for H in HZ}
pct = {}
for name, d in (("bull", v120[v120.dir == 1]), ("bear", v120[v120.dir == -1])):
    x = d["p120"]
    pct[name] = {"n": int(len(x)), "ge30pct": round(float((x >= 30).mean()), 4), "ge50pct": round(float((x >= 50).mean()), 4), "ge100pct": round(float((x >= 100).mean()), 4)}
out["pct_label_120"] = pct
by_year = {}
for (y, dr), g in v120.groupby(["year", "dir"]):
    by_year[f"{y}_{'bull' if dr == 1 else 'bear'}"] = rates(g)
out["by_year_dir_120"] = by_year
coh = {}
for (c, dr), g in v120.groupby(["cohort", "dir"]):
    coh[f"{c}_{'bull' if dr == 1 else 'bear'}"] = rates(g)
out["by_cohort_dir_120"] = coh
# denominator effect: R-based vs return-based label by ATR% tercile (within direction)
den = {}
for dr, nm in ((1, "bull"), (-1, "bear")):
    g = v120[v120.dir == dr].copy()
    g["atr_t"] = pd.qcut(g["atr_pct"], 3, labels=["low", "mid", "high"])
    for t, gg in g.groupby("atr_t", observed=True):
        den[f"{nm}_atr_{t}"] = {"n": int(len(gg)), "median_atr_pct": round(float(gg.atr_pct.median()), 2), "ge5R": round(float((gg.r120 >= 5).mean()), 4), "ge50pct": round(float((gg.p120 >= 50).mean()), 4),
                                "median_mfe_pct": round(float(gg.p120.median()), 2), "median_mfe_R": round(float(gg.r120.median()), 2)}
out["denominator_effect_120"] = den
# liquidity tercile
liq = {}
for dr, nm in ((1, "bull"),):
    g = v120[v120.dir == dr].copy()
    g["dv_t"] = pd.qcut(g["dv20"], 3, labels=["low", "mid", "high"])
    for t, gg in g.groupby("dv_t", observed=True):
        liq[f"{nm}_dollarvol_{t}"] = {"n": int(len(gg)), "median_dv20_musd": round(float(gg.dv20.median() / 1e6), 2), "ge5R": round(float((gg.r120 >= 5).mean()), 4), "ge50pct": round(float((gg.p120 >= 50).mean()), 4)}
out["liquidity_effect_120"] = liq
# MFE vs terminal value
w = v120[(v120.r120 >= 5) & (v120.dir == 1)]
out["bull_5R_winners_terminal_at_120"] = {"n": int(len(w)), "median_terminal_R": round(float(w.t120.median()), 2), "median_mfe_R": round(float(w.r120.median()), 2),
                                          "share_terminal_below_half_of_mfe": round(float((w.t120 < 0.5 * w.r120).mean()), 4), "share_terminal_negative": round(float((w.t120 < 0).mean()), 4)}
# overlap / clustering
df = df.sort_values(["sym", "dir", "i"]).reset_index(drop=True)
gap = df.groupby(["sym", "dir"])["i"].diff()
out["overlap"] = {"share_within_10_bars_of_previous_same_symbol_dir": round(float((gap <= 10).mean()), 4), "share_within_60": round(float((gap <= 60).mean()), 4), "share_within_120": round(float((gap <= 120).mean()), 4)}
# independent episodes: greedy, keep a signal only if >= 120 bars after the previous KEPT signal of the same symbol and direction
keep = np.zeros(len(df), bool)
last = {}
for j, (s_, d_, i_) in enumerate(zip(df["sym"].to_numpy(), df["dir"].to_numpy(), df["i"].to_numpy())):
    k_ = (s_, d_)
    if k_ not in last or i_ - last[k_] >= 120:
        keep[j] = True
        last[k_] = i_
ep = df[keep & df.r120.notna()]
out["episodes_120"] = {"all_signals_valid": int(df.r120.notna().sum()), "independent_episodes": int(len(ep)),
                       "bull": {"episodes": int((ep.dir == 1).sum()), "ge5R": round(float((ep[ep.dir == 1].r120 >= 5).mean()), 4), "ge10R": round(float((ep[ep.dir == 1].r120 >= 10).mean()), 4)},
                       "bear": {"episodes": int((ep.dir == -1).sum()), "ge5R": round(float((ep[ep.dir == -1].r120 >= 5).mean()), 4), "ge10R": round(float((ep[ep.dir == -1].r120 >= 10).mean()), 4)},
                       "episodes_per_year": {str(y): int(n) for y, n in ep.groupby("year").size().items()}}
per_date = df.groupby("date").size()
out["date_clustering"] = {"dates": int(len(per_date)), "mean_signals_per_date": round(float(per_date.mean()), 1), "max_signals_per_date": int(per_date.max()),
                          "share_of_signals_on_top_5pct_dates": round(float(per_date.sort_values(ascending=False).head(max(1, int(len(per_date) * 0.05))).sum() / per_date.sum()), 4)}
# market-time concentration of bullish 5R winners
bw = v120[(v120.dir == 1)].copy()
bw["ym"] = bw["date"].dt.to_period("M").astype(str)
m = bw.groupby("ym").agg(n=("r120", "size"), w=("r120", lambda x: int((x >= 5).sum()))).sort_values("w", ascending=False)
tot = m.w.sum()
k = max(1, int(round(len(m) * 0.10)))
out["month_concentration_bull_5R"] = {"months": int(len(m)), "top10pct_months_share_of_winners": round(float(m.w.head(k).sum() / tot), 4), "top10pct_months_share_of_signals": round(float(m.n.head(k).sum() / m.n.sum()), 4),
                                      "top5_months": {i: {"signals": int(r.n), "winners": int(r.w), "rate": round(r.w / r.n, 3)} for i, r in m.head(5).iterrows()},
                                      "monthly_rate_min_max_median": [round(float((m.w / m.n).min()), 3), round(float((m.w / m.n).max()), 3), round(float((m.w / m.n).median()), 3)]}
out["elapsed_s"] = round(time.time() - t0)
print("RESULT_JSON " + json.dumps(out, default=str))
