"""First catalyst experiment on REAL point-in-time events (read-only database session; writes /out/catalyst_earnings_study.json and /out/catalyst_radar_recent.json).

Chain implemented here: earnings release (report date = the 8-K Item 2.02 date) -> structured facts (EPS actual / estimate / surprise, and for 2025-26 the quarter's fundamentals)
-> anomaly rule -> catalyst class -> stock linkage (symbol) -> price reaction and post-reaction drift (market-adjusted, time-correct) -> research result.

Time correctness: the reaction window is [close before the report day, close of the day after it] (covers before-open and after-close releases, whose time of day the table does not store);
the DRIFT that a trader could capture starts at the OPEN after that window, so it never includes the reaction. Market adjustment: equal-weight index of the stored universe.
NOT yet the SEC filing itself: the live EDGAR transport needs an owner-supplied operator contact (the existing collector refuses to run without one). Vendor earnings history is flagged unverified.
"""
import json
import sys
import time
import warnings

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
sys.path[:0] = ["/app", "/app/mechanism", "/exp"]
import _common as C  # noqa: E402

T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


conn = C.connect()
mk = C.market_panel(conn)
idx = mk["idx"]
idx_dates = idx.index.to_numpy()
log("market panel", len(mk))
earn = pd.read_sql("SELECT symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at FROM earnings_calendar WHERE report_date BETWEEN DATE '2021-01-01' AND DATE '2026-10-09' "
                   "AND eps_actual IS NOT NULL AND eps_estimate IS NOT NULL AND abs(eps_estimate) >= 0.05 AND surprise_pct IS NOT NULL ORDER BY symbol, report_date", conn)
earn["report_date"] = pd.to_datetime(earn["report_date"])
earn = earn.drop_duplicates(["symbol", "report_date"])
for c in ("eps_estimate", "eps_actual", "surprise_pct"):
    earn[c] = earn[c].astype(float)
qf = pd.read_sql("SELECT symbol, quarter, revenue, net_income, operating_income, operating_margin, net_margin, revenue_growth_yoy FROM quarterly_fundamentals WHERE quarter IS NOT NULL ORDER BY symbol, quarter", conn)
qf["quarter"] = pd.to_datetime(qf["quarter"])
log("earnings events", len(earn), "symbols", earn.symbol.nunique(), "qf rows", len(qf))


def idx_at(d):
    k = int(np.searchsorted(idx_dates, np.datetime64(d)))
    return float(idx.iloc[min(k, len(idx) - 1)]) if k < len(idx) else np.nan


rows = []
skipped = {"short_history": 0, "window": 0, "illiquid_or_penny": 0, "discontinuity": 0}
for sym, g in earn.groupby("symbol"):
    px = pd.read_sql("SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s ORDER BY date", conn, params=(sym,))
    px = px.dropna(subset=["close"]).drop_duplicates("date").reset_index(drop=True)
    if len(px) < 300:
        skipped["short_history"] += len(g)
        continue
    px["date"] = pd.to_datetime(px["date"])
    o, c, v = (pd.to_numeric(px[k], errors="coerce").to_numpy(float) for k in ("open", "close", "volume"))
    dates = px["date"].to_numpy()
    dv20 = pd.Series(c * v).rolling(20, min_periods=20).mean().to_numpy()
    ret = np.r_[np.nan, c[1:] / c[:-1] - 1]
    vol20 = pd.Series(ret).rolling(20, min_periods=15).std().to_numpy()
    for r in g.itertuples(index=False):
        i1 = int(np.searchsorted(dates, np.datetime64(r.report_date)))           # first bar on/after the report date
        if i1 < 61 or i1 + 23 >= len(dates):
            skipped["window"] += 1
            continue
        pre = i1 - 1
        if not (np.isfinite(dv20[pre]) and dv20[pre] >= 5e6 and c[pre] >= 2.0):
            skipped["illiquid_or_penny"] += 1
            continue
        w = c[pre:i1 + 23]
        rr = w[1:] / w[:-1]
        if (rr > 3).any() or (rr < 1 / 3).any():
            skipped["discontinuity"] += 1
            continue
        react = c[i1 + 1] / c[pre] - 1
        m_react = idx_at(dates[i1 + 1]) / idx_at(dates[pre]) - 1
        entry = o[i1 + 2]
        d5, d20 = c[i1 + 6] / entry - 1, c[i1 + 21] / entry - 1
        m5 = idx_at(dates[i1 + 6]) / idx_at(dates[i1 + 1]) - 1
        m20 = idx_at(dates[i1 + 21]) / idx_at(dates[i1 + 1]) - 1
        rows.append((sym, r.report_date, r.eps_estimate, r.eps_actual, r.surprise_pct, react - m_react, d5 - m5, d20 - m20, float(dv20[pre]), float(vol20[pre]), float(c[pre] / c[pre - 20] - 1),
                     float(c[pre] / c[pre - 60] - 1), float(mk["b50"].asof(pd.Timestamp(dates[pre])) if True else np.nan), float(c[-1] / c[pre] - 1 - (idx.iloc[-1] / idx_at(dates[pre]) - 1))))
E = pd.DataFrame(rows, columns=["symbol", "date", "eps_est", "eps_act", "surprise", "ar_react", "ar_d5", "ar_d20", "dollar_vol20", "vol20", "ret20_pre", "ret60_pre", "b50", "ar_to_latest"])
E["month"] = E["date"].dt.to_period("M").astype(str)
log("events analysed", len(E), "skipped", skipped)

edges = [-np.inf, -10, -2, 2, 10, np.inf]
labels = ["surprise<=-10%", "-10%..-2%", "in-line(-2%..+2%)", "+2%..+10%", "surprise>=+10%"]
E["bucket"] = pd.cut(E["surprise"], edges, labels=labels, right=False)
E["big_pos"] = E["surprise"] >= 25


def stats(df, col):
    return C.block_ci(df[col].to_numpy(), df["month"].to_numpy())


res = {"design": {"events": int(len(E)), "symbols": int(E.symbol.nunique()), "first": str(E.date.min().date()), "last": str(E.date.max().date()), "skipped": skipped,
                  "filters": "price >= $2, 20-day average dollar volume >= $5M, |EPS estimate| >= 0.05, no >3x one-day move in the window",
                  "windows": "reaction = close before report day -> close of the next day (market-adjusted); drift = open after that window -> +5 / +20 sessions close (market-adjusted)",
                  "source": "earnings_calendar (vendor; fetched 2026-10-07; NOT SEC-verified; a refetch can revise history)"}}
res["by_surprise_bucket"] = {}
for b in labels + ["surprise>=+25% (subset)"]:
    d = E[E.big_pos] if b.endswith("(subset)") else E[E.bucket == b]
    res["by_surprise_bucket"][b] = {"n": int(len(d)), "ar_reaction": stats(d, "ar_react"), "ar_drift_5d": stats(d, "ar_d5"), "ar_drift_20d": stats(d, "ar_d20"),
                                    "share_positive_drift_20d": round(float((d.ar_d20 > 0).mean()), 4)}
res["by_year_drift_20d_extreme_buckets"] = {}
for b in (labels[0], labels[-1]):
    d = E[E.bucket == b]
    res["by_year_drift_20d_extreme_buckets"][b] = {int(y): {"n": int(len(g)), "mean_ar_d20": round(float(g.ar_d20.mean()), 4), "mean_ar_react": round(float(g.ar_react.mean()), 4)} for y, g in d.groupby(d.date.dt.year)}

# ------------------------------------------------------------------ fundamentals anomaly sub-study (2025-26 quarters only: that is all the table holds)
fa_rows = []
for sym, g in E.groupby("symbol"):
    q = qf[qf.symbol == sym]
    if q.empty:
        continue
    for r in g.itertuples(index=False):
        cand = q[(q.quarter <= r.date - pd.Timedelta(days=5)) & (q.quarter >= r.date - pd.Timedelta(days=130))]
        if cand.empty:
            continue
        cur = cand.iloc[-1]
        prev = q[(q.quarter < cur.quarter - pd.Timedelta(days=60)) & (q.quarter > cur.quarter - pd.Timedelta(days=130))]
        if prev.empty or pd.isna(cur.revenue_growth_yoy):
            continue
        pv = prev.iloc[-1]
        accel = (cur.revenue_growth_yoy - pv.revenue_growth_yoy) if pd.notna(pv.revenue_growth_yoy) else np.nan
        d_op = (cur.operating_margin - pv.operating_margin) if pd.notna(cur.operating_margin) and pd.notna(pv.operating_margin) else np.nan
        d_net = (cur.net_margin - pv.net_margin) if pd.notna(cur.net_margin) and pd.notna(pv.net_margin) else np.nan
        fa_rows.append((sym, r.date, float(cur.revenue_growth_yoy), float(accel) if pd.notna(accel) else np.nan, float(d_op) if pd.notna(d_op) else np.nan, float(d_net) if pd.notna(d_net) else np.nan,
                        r.ar_react, r.ar_d5, r.ar_d20, r.month))
FA = pd.DataFrame(fa_rows, columns=["symbol", "date", "rev_yoy", "rev_accel", "d_op_margin", "d_net_margin", "ar_react", "ar_d5", "ar_d20", "month"])


def fclass(r):
    if r.rev_yoy >= 25 and (r.rev_accel >= 10):
        if pd.notna(r.d_net_margin) and r.d_net_margin >= 10 and (pd.isna(r.d_op_margin) or r.d_op_margin < 2):
            return "growth_acceleration_with_net_margin_jump_not_operating (one-time suspected)"
        return "revenue_growth_acceleration (rev yoy>=25%, +10pp vs prior quarter)"
    return "no_anomaly"


if len(FA):
    FA["cls"] = FA.apply(fclass, axis=1)
    res["fundamental_anomalies"] = {"matched_events": int(len(FA)), "note": "needs a quarter row before the release and the prior quarter's growth; the table holds only 2025-26 quarters, so power is low",
                                    "by_class": {k: {"n": int(len(g)), "ar_reaction": stats(g, "ar_react"), "ar_drift_20d": stats(g, "ar_d20")} for k, g in FA.groupby("cls")}}
else:
    res["fundamental_anomalies"] = {"matched_events": 0}

# ------------------------------------------------------------------ catalyst x breakout interaction (does a recent positive surprise change a Donchian breakout's plan R?)
bo = pd.read_sql("SELECT symbol, date, direction, plan_r FROM ml_breakout_dataset_v2 WHERE feature_set_version = 'price_v1' AND plan_r IS NOT NULL AND date >= DATE '2021-01-01' ORDER BY date", conn)
bo["date"] = pd.to_datetime(bo["date"])
pos = E[E.surprise >= 10][["symbol", "date", "surprise"]].rename(columns={"date": "ev_date"}).sort_values("ev_date")
neg = E[E.surprise <= -10][["symbol", "date", "surprise"]].rename(columns={"date": "ev_date"}).sort_values("ev_date")
bo = bo.sort_values("date")
for nm, ev_ in (("recent_pos_surprise", pos), ("recent_neg_surprise", neg)):
    m = pd.merge_asof(bo[["symbol", "date"]], ev_, left_on="date", right_on="ev_date", by="symbol", direction="backward", tolerance=pd.Timedelta(days=14), allow_exact_matches=False)
    bo[nm] = m["ev_date"].notna().to_numpy()
bo["month"] = bo["date"].dt.to_period("M").astype(str)
inter = {}
for dn, nm in ((1, "bullish"), (-1, "bearish")):
    g = bo[bo.direction == dn]
    inter[nm] = {"all": C.block_ci(g.plan_r.to_numpy(), g.month.to_numpy()),
                 "after_positive_surprise_10pct_within_14d": C.block_ci(g[g.recent_pos_surprise].plan_r.to_numpy(), g[g.recent_pos_surprise].month.to_numpy()),
                 "after_negative_surprise_10pct_within_14d": C.block_ci(g[g.recent_neg_surprise].plan_r.to_numpy(), g[g.recent_neg_surprise].month.to_numpy()),
                 "no_recent_big_surprise": C.block_ci(g[~g.recent_pos_surprise & ~g.recent_neg_surprise].plan_r.to_numpy(), g[~g.recent_pos_surprise & ~g.recent_neg_surprise].month.to_numpy())}
res["catalyst_x_breakout_plan_r"] = inter
log("interaction done")

# ------------------------------------------------------------------ ML research targets with chronological evaluation and simple baselines
split, emb = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-15")
E["abs_react"] = E["ar_react"].abs()
E["surprise_abs"] = E["surprise"].abs().clip(upper=300)
tr = E[E.date < split]
thr = tr["abs_react"].quantile(0.9)                                   # threshold fixed on TRAIN only
E["big_move"] = (E["abs_react"] >= thr).astype(int)
tr, te = E[E.date < split], E[E.date >= emb]
F1 = ["surprise", "vol20", "ret20_pre", "ret60_pre", "dollar_vol20", "b50"]
F1abs = ["surprise_abs"] + F1
PAR = dict(n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=50, reg_lambda=5.0, tree_method="hist", random_state=42, n_jobs=2)
ml = {"split": {"train_until": str(split.date()), "test_from": str(emb.date()), "n_train": int(len(tr)), "n_test": int(len(te)), "big_move_threshold_abs_ar_reaction": round(float(thr), 4)}}
m = xgb.XGBClassifier(**PAR).fit(tr[F1abs], tr["big_move"])
ml["target1_event_significance_big_move"] = {"auc_model": round(float(roc_auc_score(te["big_move"], m.predict_proba(te[F1abs])[:, 1])), 4),
                                             "auc_baseline_trailing_volatility_only": round(float(roc_auc_score(te["big_move"], te["vol20"])), 4),
                                             "auc_baseline_abs_surprise_only": round(float(roc_auc_score(te["big_move"], te["surprise_abs"])), 4),
                                             "base_rate_test": round(float(te["big_move"].mean()), 4),
                                             "note": "the surprise is the event's own fact, known at release; the question answered is how significant a release will be, not a forecast before it"}
pos_tr, pos_te = tr[tr.surprise >= 10], te[te.surprise >= 10]
F2 = ["surprise_abs", "ar_react", "vol20", "ret20_pre", "ret60_pre", "dollar_vol20", "b50"]
y_tr, y_te = (pos_tr["ar_d20"] > 0).astype(int), (pos_te["ar_d20"] > 0).astype(int)
m2 = xgb.XGBClassifier(**PAR).fit(pos_tr[F2], y_tr)
ml["target2_post_reaction_drift_after_positive_surprise"] = {"n_train": int(len(pos_tr)), "n_test": int(len(pos_te)), "auc_model": round(float(roc_auc_score(y_te, m2.predict_proba(pos_te[F2])[:, 1])), 4),
                                                            "auc_baseline_reaction_only": round(float(roc_auc_score(y_te, pos_te["ar_react"])), 4), "base_rate_test": round(float(y_te.mean()), 4),
                                                            "mean_ar_d20_test": round(float(pos_te["ar_d20"].mean()), 4)}
res["ml_targets"] = ml
log("ml done", ml["target1_event_significance_big_move"]["auc_model"])

# ------------------------------------------------------------------ recent unusual events (the Catalyst Radar feed): last 40 days, with outcome so far
recent = E[(E.date >= E.date.max() - pd.Timedelta(days=40)) & (E.surprise >= 25)].sort_values("date", ascending=False).head(40)
radar = []
fa_idx = FA.set_index(["symbol", "date"]) if len(FA) else None
for r in recent.itertuples(index=False):
    fa_cls = None
    if fa_idx is not None and (r.symbol, r.date) in fa_idx.index:
        fa_cls = fa_idx.loc[(r.symbol, r.date)]
        fa_cls = fa_cls.iloc[0]["cls"] if isinstance(fa_cls, pd.DataFrame) else fa_cls["cls"]
    radar.append({"symbol": r.symbol, "report_date": str(r.date.date()), "eps_estimate": r.eps_est, "eps_actual": r.eps_act, "surprise_pct": round(r.surprise, 1),
                  "abnormal_reaction_pct": round(100 * r.ar_react, 2), "abnormal_return_to_latest_close_pct": round(100 * r.ar_to_latest, 2),
                  "fundamental_class": fa_cls, "verification": "vendor-reported (earnings_calendar), not SEC-verified", "source": "earnings_calendar", "fetched_at": "2026-10-07"})
json.dump({"generated_for": "research evidence; not an alert", "events": radar}, open("/out/catalyst_radar_recent.json", "w"), indent=1)
json.dump(res, open("/out/catalyst_earnings_study.json", "w"), indent=1, default=str)
log("WROTE catalyst_earnings_study.json and catalyst_radar_recent.json")
