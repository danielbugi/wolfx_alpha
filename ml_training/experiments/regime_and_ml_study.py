"""Quant Lab historical experiments, run against an ISOLATED scratch copy of the production database. Read-only on the database; writes only /out.
Everything is computed from real rows: ml_breakout_dataset_v2 (price-only features + trade-plan outcomes) and stock_prices. No simulated data, no tuned thresholds on the holdout.
Plan outcome definitions are the repository's own (ml_training/features/price_features.py): plan_r = R of the 2xATR-stop / 2-4-6xATR tranche plan over 20 bars.
"""
import json
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
import xgboost as xgb
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")
from ml_training.features import price_features as pf  # noqa: E402

OUT = "/out"
T0 = time.time()
R = {}


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                        options="-c default_transaction_read_only=on -c statement_timeout=1800000")

# ------------------------------------------------------------------------------------------------ 1. dataset and data quality
log("loading dataset")
df = pd.read_sql("SELECT symbol, date, direction, features, plan_r, r_single, stopped, tp3_hit, mae_r, target_binary FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s ORDER BY date, symbol", conn,
                 params=(pf.FEATURE_SET_VERSION,))
df["date"] = pd.to_datetime(df["date"])
feat = pd.DataFrame(df.pop("features").tolist(), index=df.index)
feat = feat.reindex(columns=pf.FEATURE_NAMES).astype("float32")
log("rows", len(df), "features", feat.shape)
mature = df["plan_r"].notna()
dq = {
    "rows": int(len(df)), "symbols": int(df["symbol"].nunique()), "first": str(df["date"].min().date()), "last": str(df["date"].max().date()),
    "rows_with_plan_outcome": int(mature.sum()), "rows_without_outcome_yet": int((~mature).sum()),
    "bullish_share": round(float((df["direction"] == 1).mean()), 4),
    "by_year": {int(y): {"n": int(len(g)), "bullish": int((g.direction == 1).sum()), "mature": int(g.plan_r.notna().sum())} for y, g in df.groupby(df.date.dt.year)},
    "feature_nan_share": {c: round(float(feat[c].isna().mean()), 4) for c in pf.FEATURE_NAMES if feat[c].isna().mean() > 0.01},
    "duplicate_symbol_date": int(df.duplicated(["symbol", "date"]).sum()),
    "plan_r_range": [round(float(df.plan_r.min()), 3), round(float(df.plan_r.max()), 3)],
}
cur = conn.cursor()
cur.execute("SELECT count(*), count(DISTINCT symbol) FROM price_discontinuities")
dq["price_discontinuity_rows_symbols"] = list(cur.fetchone())
cur.execute("SELECT min(date), max(date), count(*), count(DISTINCT symbol) FROM stock_prices")
a = cur.fetchone()
dq["prices"] = {"first": str(a[0]), "last": str(a[1]), "rows": a[2], "symbols": a[3]}
R["data_quality"] = dq
log("data quality done")

d = df[mature].copy()
f = feat[mature.values].copy()


def date_block_stats(g, col="plan_r"):
    """Mean R with a date-clustered (month-block) bootstrap CI: trades on the same days are not independent."""
    n = len(g)
    if n == 0:
        return {"n": 0}
    m = g[col].mean()
    month = g["date"].dt.to_period("M")
    blocks = [x[col].to_numpy() for _, x in g.groupby(month)]
    rng = np.random.default_rng(7)
    means = []
    for _ in range(300):
        pick = rng.integers(0, len(blocks), len(blocks))
        v = np.concatenate([blocks[i] for i in pick])
        means.append(v.mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": int(n), "mean_r": round(float(m), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)], "win_rate_r_gt_0": round(float((g[col] > 0).mean()), 4),
            "stopped": round(float(g["stopped"].mean()), 4), "tp3_hit": round(float(g["tp3_hit"].mean()), 4)}


# ------------------------------------------------------------------------------------------------ 2. strategy baseline
base = {"all": date_block_stats(d), "bullish": date_block_stats(d[d.direction == 1]), "bearish": date_block_stats(d[d.direction == -1])}
base["by_year"] = {int(y): {"bullish": date_block_stats(g[g.direction == 1]), "bearish": date_block_stats(g[g.direction == -1])} for y, g in d.groupby(d.date.dt.year)}
R["strategy_baseline"] = base
log("baseline done", base["bullish"].get("mean_r"), base["bearish"].get("mean_r"))

# ------------------------------------------------------------------------------------------------ 3. market regime from the price panel itself (point-in-time: only data <= date)
log("computing breadth / equal-weight index from stock_prices (SQL)")
q = """
WITH p AS (
  SELECT symbol, date, close,
         close / NULLIF(lag(close) OVER w, 0) - 1 AS ret,
         avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
         avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200,
         count(*) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n200
  FROM stock_prices WHERE date >= DATE '2016-09-01' AND close > 0
  WINDOW w AS (PARTITION BY symbol ORDER BY date))
SELECT date, count(*) AS n, avg(CASE WHEN ret BETWEEN -0.5 AND 0.5 THEN ret END) AS ew_ret,
       avg((close > sma50)::int) FILTER (WHERE n200 >= 50) AS b50, avg((close > sma200)::int) FILTER (WHERE n200 >= 200) AS b200
FROM p GROUP BY date HAVING count(*) > 1500 ORDER BY date"""
mk = pd.read_sql(q, conn)
mk["date"] = pd.to_datetime(mk["date"])
mk = mk.set_index("date")
mk["ew_ret"] = mk["ew_ret"].fillna(0.0)
mk["idx"] = (1 + mk["ew_ret"]).cumprod()
mk["idx_sma200"] = mk["idx"].rolling(200, min_periods=150).mean()
mk["idx_above_200"] = (mk["idx"] > mk["idx_sma200"]).astype(float).where(mk["idx_sma200"].notna())
mk["idx_ret20"] = mk["idx"].pct_change(20)
mk["b50_chg10"] = mk["b50"] - mk["b50"].shift(10)
log("market panel", len(mk), "sessions", str(mk.index.min().date()), str(mk.index.max().date()))
R["market_panel"] = {"sessions": int(len(mk)), "first": str(mk.index.min().date()), "last": str(mk.index.max().date()),
                     "note": "equal-weight index of all stored symbols (survivorship: only symbols still in the table); breadth = share of symbols above their own SMA50/SMA200"}

# regime on the signal date (information available at the close of that date)
reg = mk[["b50", "b200", "idx_above_200", "idx_ret20", "b50_chg10"]].reindex(d["date"]).reset_index(drop=True)
reg.index = d.index
d = d.join(reg)
f = f.join(reg.astype("float32"))

# fixed a-priori regime definitions (not tuned): breadth50 >= 0.5 'broad'; index above its 200-day average 'uptrend'
d["regime_broad"] = np.where(d["b50"].isna(), np.nan, (d["b50"] >= 0.5).astype(float))
d["regime_up"] = d["idx_above_200"]
res = {}
for dirn, name in ((1, "bullish"), (-1, "bearish")):
    g = d[d.direction == dirn]
    res[name] = {
        "uptrend_idx_above_200dma": date_block_stats(g[g.regime_up == 1]), "downtrend_idx_below_200dma": date_block_stats(g[g.regime_up == 0]),
        "broad_breadth50_ge_0.5": date_block_stats(g[g.regime_broad == 1]), "narrow_breadth50_lt_0.5": date_block_stats(g[g.regime_broad == 0]),
    }
    # stability: the same split by year (sign consistency is the evidence, not one pooled number)
    res[name]["by_year_uptrend_vs_downtrend_mean_r"] = {int(y): {"up": round(float(x[x.regime_up == 1].plan_r.mean()), 4) if (x.regime_up == 1).any() else None,
                                                                 "down": round(float(x[x.regime_up == 0].plan_r.mean()), 4) if (x.regime_up == 0).any() else None,
                                                                 "n_up": int((x.regime_up == 1).sum()), "n_down": int((x.regime_up == 0).sum())} for y, x in g.groupby(g.date.dt.year)}
R["regime_study"] = res
# the actionable filter, pre-specified: bullish only in uptrend, bearish only in downtrend
filt = d[((d.direction == 1) & (d.regime_up == 1)) | ((d.direction == -1) & (d.regime_up == 0))]
R["regime_filter_trend_following"] = {"all_years": date_block_stats(filt), "unfiltered_all": date_block_stats(d),
                                      "by_year": {int(y): {"filtered": date_block_stats(g)["mean_r"] if len(g) else None, "n": int(len(g)),
                                                           "unfiltered": round(float(d[d.date.dt.year == y].plan_r.mean()), 4)} for y, g in filt.groupby(filt.date.dt.year)}}
log("regime study done")

# ------------------------------------------------------------------------------------------------ 4. ML: does ANY feature set rank the trade plan outcome out of sample?
split = pd.Timestamp("2024-01-01")
embargo = pd.Timestamp("2024-02-15")
tr = (d.date < split).to_numpy()
te = (d.date >= embargo).to_numpy()
log("ML train", int(tr.sum()), "test", int(te.sum()))
PAR = dict(max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=100, reg_lambda=5.0, tree_method="hist", random_state=42, n_jobs=2)
ytr_c = (d.plan_r[tr] > 0).astype(int).to_numpy()
ytr_r = d.plan_r[tr].clip(-3, 6).to_numpy()


def quint(score, y):
    q = pd.qcut(pd.Series(score).rank(method="first"), 5, labels=False)
    return [round(float(y[q.to_numpy() == i].mean()), 4) for i in range(5)]


def evaluate(name, cols):
    X = f[cols]
    out = {"features": len(cols)}
    # classification: R > 0
    m = xgb.XGBClassifier(n_estimators=250, **PAR, eval_metric="logloss")
    m.fit(X[tr], ytr_c)
    s = m.predict_proba(X[te])[:, 1]
    y_te = d.plan_r[te].to_numpy()
    out["auc_r_gt_0"] = round(float(roc_auc_score((y_te > 0).astype(int), s)), 4)
    out["auc_within_bullish"] = round(float(roc_auc_score((y_te[d.direction[te].to_numpy() == 1] > 0).astype(int), s[d.direction[te].to_numpy() == 1])), 4)
    out["auc_within_bearish"] = round(float(roc_auc_score((y_te[d.direction[te].to_numpy() == -1] > 0).astype(int), s[d.direction[te].to_numpy() == -1])), 4)
    out["mean_r_by_score_quintile_low_to_high"] = quint(s, y_te)
    out["mean_r_all_test"] = round(float(y_te.mean()), 4)
    top = s >= np.quantile(s, 0.9)
    gt = d[te].copy()
    gt["score"] = s
    out["top_decile"] = date_block_stats(gt[top])
    out["spearman_score_vs_r"] = round(float(pd.Series(s).corr(pd.Series(y_te), method="spearman")), 4)
    imp = sorted(zip(cols, m.feature_importances_), key=lambda t: -t[1])[:6]
    out["top_features"] = [(a, round(float(b), 4)) for a, b in imp]
    return out


base_cols = list(pf.FEATURE_NAMES)
regime_cols = ["b50", "b200", "idx_above_200", "idx_ret20", "b50_chg10"]
ml = {"split": {"train_until": str(split.date()), "test_from": str(embargo.date()), "n_train": int(tr.sum()), "n_test": int(te.sum()),
                "note": "fixed a priori, no tuning on the test period; label = plan_r > 0 (the exact trade-plan outcome)"}}
ml["direction_only_baseline"] = {"test_mean_r_bullish": round(float(d.plan_r[te][d.direction[te] == 1].mean()), 4), "test_mean_r_bearish": round(float(d.plan_r[te][d.direction[te] == -1].mean()), 4)}
ml["price_features_only"] = evaluate("price", base_cols)
log("ML price-only", ml["price_features_only"]["auc_r_gt_0"])
ml["price_plus_market_regime"] = evaluate("price+regime", base_cols + regime_cols)
log("ML price+regime", ml["price_plus_market_regime"]["auc_r_gt_0"])
no_vol = [c for c in base_cols if c not in ("vol_ratio_10", "vol_ratio_50", "log_dollar_vol_20")]
ml["no_volume_features"] = evaluate("no_volume", no_vol)
R["ml_plan_outcome"] = ml

# simple, interpretable rankers (no fitting at all) for comparison
simple = {}
gte = d[te].copy()
ftest = f[te]
for name, sc in (("low_atr_pct", -ftest["atr_pct"]), ("near_52w_high_bullish_far_bearish", ftest["pct_from_52w_high"] * np.where(d.direction[te] == 1, 1, -1)),
                 ("strong_ret_60d_in_direction", ftest["ret_60d"] * d.direction[te].to_numpy()), ("tight_channel_squeeze", -ftest["chan_squeeze_pctile"])):
    simple[name] = {"mean_r_by_quintile_low_to_high": quint(sc.to_numpy(), gte.plan_r.to_numpy()), "spearman": round(float(pd.Series(sc.to_numpy()).corr(gte.plan_r.reset_index(drop=True), method="spearman")), 4)}
R["simple_rankers_test_period"] = simple
log("done")

R["meta"] = {"feature_set_version": pf.FEATURE_SET_VERSION, "elapsed_s": round(time.time() - T0), "image_note": "mechanism image 5c511899505a on an isolated copy of the 2026-10-10 production backup"}
json.dump(R, open(f"{OUT}/experiments.json", "w"), indent=1, default=str)
print("WROTE experiments.json")
