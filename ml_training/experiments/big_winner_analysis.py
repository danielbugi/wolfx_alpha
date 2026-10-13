"""Big-winner intelligence analysis (research only; pure functions over arrays/DataFrames, no database).

Questions, in the owner's words: where do the large winners come from, how much of them does each exit policy capture, which exits are premature, and which DECISION-TIME conditions preceded exceptional
moves. Two kinds of information are kept apart everywhere:
  * hindsight (`hs_*`): favourable excursion, tiers, capture, missed continuation: they need bars after the decision and are only ever LABELS or outcomes;
  * decision-time (`pit_*`): relative volume, trend, volatility, index regime, the catalyst known by then: the only inputs a model or a lift table may use.
A train row whose hindsight label window reaches into the test period is purged (the label would overlap it). Results are research evidence: a lift or an AUC here is not an edge until it survives forward
observation, and nothing in this module is a trading rule.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

BIG_R = 5.0                  # a "big winner": favourable excursion of at least 5R (10 ATR) within 120 bars (hindsight label)
LABEL_BARS = 120
LABEL_PURGE_DAYS = 180       # calendar days: a train signal this close to the test start has a label window that overlaps the test period


def purge_split(signal_dates: Sequence[Any], train_end, test_start, label_purge_days: int = LABEL_PURGE_DAYS) -> np.ndarray:
    """'train' | 'embargo' | 'purged' | 'test' by signal date; train signals within `label_purge_days` of the test start are purged (their label window reaches into the test period)."""
    d = pd.to_datetime(pd.Series(signal_dates))
    te, ts = pd.Timestamp(train_end), pd.Timestamp(test_start)
    out = np.where(d >= ts, "test", np.where(d >= te, "embargo", np.where(d >= ts - pd.Timedelta(days=label_purge_days), "purged", "train")))
    return out


def tail_summary(values: Sequence[float]) -> Dict[str, Any]:
    v = np.asarray([x for x in values if x is not None and math.isfinite(x)], dtype=float)
    if len(v) == 0:
        return {"n": 0}
    pos = np.sort(v[v > 0])[::-1]
    tot = float(pos.sum())
    out: Dict[str, Any] = {"n": int(len(v)), "mean": round(float(v.mean()), 4), "median": round(float(np.median(v)), 4), "p90": round(float(np.percentile(v, 90)), 4), "p99": round(float(np.percentile(v, 99)), 4)}
    for f in (0.01, 0.05, 0.10):
        k = max(1, int(round(len(v) * f)))
        out[f"top_{int(f * 100)}pct_share_of_positive"] = round(float(pos[:k].sum() / tot), 4) if tot > 0 else None
    sd = float(v.std())
    out["skewness"] = round(float(((v - v.mean()) ** 3).mean() / sd ** 3), 3) if sd > 0 else None
    return out


def opportunity_distribution(fav_r: Mapping[int, np.ndarray], direction: np.ndarray) -> Dict[str, Any]:
    """Hindsight distribution of the favourable excursion (in R) by horizon and direction, and the share of signals reaching each big-winner tier within 120 bars."""
    out: Dict[str, Any] = {}
    for dn, dv in (("all", None), ("bullish", 1), ("bearish", -1)):
        m = np.ones(len(direction), bool) if dv is None else direction == dv
        row: Dict[str, Any] = {"n_signals": int(m.sum()), "by_horizon": {}}
        for H, arr in fav_r.items():
            a = arr[m]
            ok = np.isfinite(a)
            row["by_horizon"][str(H)] = {**tail_summary(a[ok]), "n_unknown": int((~ok).sum())}
        a120 = fav_r[LABEL_BARS][m]
        ok = np.isfinite(a120)
        row["tiers_120"] = {f"ge_{int(t)}r": round(float((a120[ok] >= t).mean()), 4) if ok.any() else None for t in (3.0, 5.0, 10.0)}
        out[dn] = row
    return out


def capture_by_variant(names: Sequence[str], realized_r: np.ndarray, mfe_r: np.ndarray, capture_ratio: np.ndarray, missed_cont_r: np.ndarray, premature: np.ndarray, big: np.ndarray,
                       taken: np.ndarray) -> Dict[str, Any]:
    """Per variant (one column each): among the big-winner signals the variant took, how much of the favourable move it kept, how many exits were premature, and the payoff asymmetry of all its positions.
    `realized_r` etc. are (signals x variants) arrays in the standardised unit; `taken` marks the positions the one-open-position rule accepted."""
    out: Dict[str, Any] = {}
    for j, n in enumerate(names):
        t = taken[:, j] & np.isfinite(realized_r[:, j])
        bw = t & big
        cap = capture_ratio[bw & np.isfinite(capture_ratio[:, j]), j]
        out[n] = {"positions": int(t.sum()), "big_winner_positions": int(bw.sum()),
                  "big_winner_capture_ratio": {"median": round(float(np.median(cap)), 4) if len(cap) else None, "mean": round(float(cap.mean()), 4) if len(cap) else None, "n": int(len(cap))},
                  "big_winner_mean_realized_r": round(float(realized_r[bw, j].mean()), 4) if bw.any() else None, "big_winner_mean_mfe_r": round(float(mfe_r[bw, j].mean()), 4) if bw.any() else None,
                  "big_winner_premature_exit_share": round(float(premature[bw & np.isfinite(premature[:, j]), j].mean()), 4) if (bw & np.isfinite(premature[:, j])).any() else None,
                  "all_premature_exit_share": round(float(premature[t & np.isfinite(premature[:, j]), j].mean()), 4) if (t & np.isfinite(premature[:, j])).any() else None,
                  "all_mean_missed_continuation_r": round(float(missed_cont_r[t & np.isfinite(missed_cont_r[:, j]), j].mean()), 4) if (t & np.isfinite(missed_cont_r[:, j])).any() else None,
                  "realized_r_tail": tail_summary(realized_r[t, j]),
                  "share_of_big_winners_taken_and_ended_positive": round(float((realized_r[bw, j] > 0).mean()), 4) if bw.any() else None}
    return out


def _block_ci(values: np.ndarray, months: np.ndarray, n_boot: int = 300, seed: int = 11):
    df = pd.DataFrame({"v": values, "m": months})
    blocks = [g["v"].to_numpy() for _, g in df.groupby("m")]
    if len(blocks) < 3:
        return None
    rng = np.random.default_rng(seed)
    means = [np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(n_boot)]
    return [round(float(x), 4) for x in np.percentile(means, [2.5, 97.5])]


def lift_table(feature: pd.Series, label: np.ndarray, split: np.ndarray, months: np.ndarray, min_n: int = 100) -> Dict[str, Any]:
    """P(big winner | bucket) against the base rate for the TRAIN and the TEST period separately, per decision-time bucket (a categorical or pre-binned Series). Test lift carries a month-block interval."""
    out: Dict[str, Any] = {}
    for part in ("train", "test"):
        m = split == part
        base = float(label[m].mean()) if m.any() else None
        rows = {}
        for b in sorted(pd.unique(feature[m].dropna()), key=str):
            mb = m & (feature.to_numpy() == b)
            if mb.sum() < min_n:
                continue
            p = float(label[mb].mean())
            row = {"n": int(mb.sum()), "rate": round(p, 4), "lift": round(p / base, 3) if base else None}
            if part == "test":
                row["rate_ci95"] = _block_ci(label[mb].astype(float), months[mb])
            rows[str(b)] = row
        out[part] = {"base_rate": round(base, 4) if base is not None else None, "n": int(m.sum()), "buckets": rows}
    return out


def auc(y: np.ndarray, score: np.ndarray) -> Optional[float]:
    """Rank AUC (Mann-Whitney) with ties handled; None when one class is missing."""
    y = np.asarray(y).astype(int)
    s = np.asarray(score, dtype=float)
    ok = np.isfinite(s)
    y, s = y[ok], s[ok]
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    if n1 == 0 or n0 == 0:
        return None
    ranks = pd.Series(s).rank(method="average").to_numpy()
    return round(float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 4)


def precision_at_top(y: np.ndarray, score: np.ndarray, frac: float = 0.10) -> Dict[str, Any]:
    ok = np.isfinite(score)
    y, s = np.asarray(y)[ok], np.asarray(score)[ok]
    k = max(1, int(len(y) * frac))
    top = np.argsort(-s, kind="stable")[:k]
    base = float(y.mean())
    p = float(y[top].mean())
    return {"top_fraction": frac, "precision": round(p, 4), "base_rate": round(base, 4), "lift": round(p / base, 3) if base > 0 else None, "n_top": int(k)}


def model_baselines(X: pd.DataFrame, y: np.ndarray, split: np.ndarray, single_feature_baselines: Sequence[str], params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Chronological evaluation of a small gradient-boosted model on DECISION-TIME features against single-feature baselines (volatility, relative volume, ...), all judged on the test period with
    AUC and precision at the top decile. The model never sees a hindsight column; the label purge is already applied through `split`. Research evidence only."""
    import xgboost as xgb
    tr, te = split == "train", split == "test"
    out: Dict[str, Any] = {"n_train": int(tr.sum()), "n_test": int(te.sum()), "base_rate_train": round(float(y[tr].mean()), 4) if tr.any() else None, "base_rate_test": round(float(y[te].mean()), 4) if te.any() else None,
                           "features": list(X.columns)}
    if tr.sum() < 500 or te.sum() < 500 or y[tr].sum() < 20 or y[te].sum() < 20:
        return {**out, "note": "too few positives to evaluate"}
    p = dict(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=50, reg_lambda=5.0, tree_method="hist", random_state=42, n_jobs=2)
    p.update(params or {})
    m = xgb.XGBClassifier(**p).fit(X[tr], y[tr])
    score = m.predict_proba(X[te])[:, 1]
    out["model"] = {"auc": auc(y[te], score), **precision_at_top(y[te], score)}
    out["baselines"] = {c: {"auc": auc(y[te], X.loc[te, c].to_numpy(dtype=float)), **precision_at_top(y[te], X.loc[te, c].to_numpy(dtype=float))} for c in single_feature_baselines}
    imp = dict(zip(X.columns, m.feature_importances_))
    out["feature_importance"] = {k: round(float(v), 4) for k, v in sorted(imp.items(), key=lambda kv: -kv[1])}
    return out
