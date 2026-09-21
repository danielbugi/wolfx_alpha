# ml_training/models/momentum_predictor.py
"""
XGBoost breakout model -- honest-evaluation trainer (rewritten 2026-09-20).

What was wrong with the previous trainer (ML audit 2026-09-20):
  * it loaded rows `ORDER BY date DESC` and split by position, so it trained on the NEWEST 80%
    and tested on the OLDEST 20%; early stopping also used that test set. The reported AUC 0.70
    reproduced exactly, and dropped to ~0.56 on a real chronological split;
  * scaler / median-fill / 3-sigma clipping were fitted on the full dataset before splitting;
  * a model was promoted if AUC > 0.6 on that inflated number.

This trainer:
  * reads ml_breakout_dataset_v2 (built from stock_prices only by build_dataset.py) -- features come
    from ml_training/features/price_features.py, the SAME code the live enhancer calls;
  * chronological holdout (last `holdout_frac` of dates) with an embargo >= label window, early stopping
    on an inner validation slice carved from the training period, never on the holdout;
  * no scaler, no imputation, no clipping (trees; NaN stays NaN);
  * reports base rate, AUC with date-block bootstrap CI, per-direction AUC, direction-only baseline,
    Brier vs base-rate Brier, top-decile lift, calibration bins, walk-forward fold stability, and
    the correlation of the score with the R-outcome of the trade plan the UI shows;
  * promotes a model ONLY if it passes the gate below; otherwise writes a candidate report and saves
    nothing where the live screener would auto-load it.

Usage (repo root):
    python ml_training/models/momentum_predictor.py                    # target = legacy momentum >= 65
    python ml_training/models/momentum_predictor.py --target plan_profit
    python ml_training/models/momentum_predictor.py --no-promote       # evaluate only
"""
import argparse
import json
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import psycopg2
import xgboost as xgb
import joblib
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ml_training", "config"))

from ml_config import ml_config  # noqa: E402
from ml_training.features import price_features as pf  # noqa: E402

# Fixed a priori -- NOT tuned on the holdout. Deliberately conservative: the signal is weak and
# noisy, so large leaves + shrinkage + L2 rather than capacity.
XGB_PARAMS = dict(max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                  min_child_weight=50, reg_lambda=5.0, tree_method="hist", random_state=42,
                  eval_metric="logloss", n_jobs=4)
MAX_TREES = 600
EARLY_STOP = 30

EMBARGO_DAYS = 30            # >= legacy label window (25 calendar days) so no label straddles the boundary
HOLDOUT_FRAC = 0.20          # last 20% of trading dates
INNER_VAL_FRAC = 0.15        # last 15% of training dates -> early stopping only
WF_FOLDS = 6
BOOTSTRAP_REPS = 300

# Promotion gate (a priori). All must hold on the untouched holdout / walk-forward.
GATE = dict(min_holdout_auc=0.55, min_auc_ci_low=0.50, min_auc_over_direction_baseline=0.02,
            min_wf_share_above_half=0.75, min_top_decile_lift=1.15,
            # Added after the first run (2026-09-20), disclosed: the legacy label contains a mechanical
            # volume term, so predicting it well says nothing about trading value. The score must also
            # rank the P&L of the plan the UI shows, WITHIN each direction (so a bullish-vs-bearish mix
            # effect cannot pass it).
            min_plan_profit_auc_within_direction=0.52)

TARGETS = {
    "momentum": ("target_binary", "Legacy composite momentum score >= 65 within 25 calendar days"),
    "plan_profit": ("plan_profit", "Trade plan (2xATR stop, 2/4/6xATR tranches, 20 bars) ends with R > 0"),
}


class MomentumBreakoutPredictor:
    def __init__(self, target="momentum", exclude=()):
        self.db_config = ml_config.db_config
        self.target = target
        self.model = None
        self.exclude = list(exclude)
        self.feature_names = [f for f in pf.FEATURE_NAMES if f not in self.exclude]
        self.model_version = None

    # ------------------------------------------------------------------ data
    def load_dataset(self, min_date=None):
        conn = psycopg2.connect(**self.db_config)
        q = ("SELECT symbol, date, direction, features, target_binary, plan_r, r_single, stopped, tp3_hit "
             "FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s "
             "AND target_binary IS NOT NULL AND plan_r IS NOT NULL")
        params = [pf.FEATURE_SET_VERSION]
        if min_date:
            q += " AND date >= %s"
            params.append(min_date)
        df = pd.read_sql(q, conn, params=params)
        conn.close()
        if df.empty:
            raise SystemExit("ml_breakout_dataset_v2 has no mature rows -- run build_dataset.py first")
        feats = pd.DataFrame([f if isinstance(f, dict) else json.loads(f) for f in df.pop("features")])
        missing = [c for c in self.feature_names if c not in feats.columns]
        if missing:
            raise SystemExit(f"dataset lacks features {missing} -- rebuild with the current feature set")
        X = feats[self.feature_names].astype(float)
        df["date"] = pd.to_datetime(df["date"])
        df["plan_profit"] = (df["plan_r"] > 0).astype(int)
        y_col = TARGETS[self.target][0]
        y = df[y_col].astype(int)
        order = np.argsort(df["date"].to_numpy(), kind="stable")
        return X.iloc[order].reset_index(drop=True), y.iloc[order].reset_index(drop=True), df.iloc[order].reset_index(drop=True)

    # ------------------------------------------------------------------ fitting
    @staticmethod
    def _fit(Xtr, ytr, Xval=None, yval=None, n_trees=None):
        params = dict(XGB_PARAMS)
        if Xval is not None:
            m = xgb.XGBClassifier(n_estimators=MAX_TREES, early_stopping_rounds=EARLY_STOP, **params)
            m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
        else:
            m = xgb.XGBClassifier(n_estimators=n_trees, **params)
            m.fit(Xtr, ytr, verbose=False)
        return m

    def _train_with_inner_validation(self, X, y, dates):
        """Early-stop on the tail of the training period; refit on all of it with the chosen tree count."""
        cut = dates.quantile(1 - INNER_VAL_FRAC, interpolation="lower")
        fit_idx = dates < cut - pd.Timedelta(days=EMBARGO_DAYS)
        val_idx = dates >= cut
        m0 = self._fit(X[fit_idx], y[fit_idx], X[val_idx], y[val_idx])
        n_trees = max(20, int((m0.best_iteration + 1) * 1.1))
        return self._fit(X, y, n_trees=n_trees), n_trees

    # ------------------------------------------------------------------ evaluation
    @staticmethod
    def _block_bootstrap_auc(y, p, dates, reps=BOOTSTRAP_REPS, seed=7):
        wk = dates.dt.to_period("W").astype(str).to_numpy()
        groups = {w: np.flatnonzero(wk == w) for w in np.unique(wk)}
        keys = list(groups)
        rng = np.random.default_rng(seed)
        aucs = []
        for _ in range(reps):
            pick = rng.choice(len(keys), len(keys), replace=True)
            idx = np.concatenate([groups[keys[k]] for k in pick])
            if len(np.unique(y[idx])) == 2:
                aucs.append(roc_auc_score(y[idx], p[idx]))
        return float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))

    def evaluate(self, y, p, meta, direction_baseline_p):
        yv, pv = y.to_numpy(), np.asarray(p)
        base = float(yv.mean())
        out = {"n": int(len(yv)), "base_rate": round(base, 4),
               "auc": round(roc_auc_score(yv, pv), 4),
               "direction_baseline_auc": round(roc_auc_score(yv, direction_baseline_p), 4),
               "brier": round(brier_score_loss(yv, pv), 5),
               "brier_base_rate": round(brier_score_loss(yv, np.full(len(yv), base)), 5),
               "logloss": round(log_loss(yv, np.clip(pv, 1e-6, 1 - 1e-6)), 5)}
        lo, hi = self._block_bootstrap_auc(yv, pv, meta["date"].reset_index(drop=True))
        out["auc_ci95"] = [round(lo, 4), round(hi, 4)]
        for name, d in (("bullish", 1), ("bearish", -1)):
            m = (meta["direction"] == d).to_numpy()
            out[f"auc_{name}"] = round(roc_auc_score(yv[m], pv[m]), 4) if len(np.unique(yv[m])) == 2 else None
            out[f"n_{name}"] = int(m.sum())
        top = pv >= np.quantile(pv, 0.9)
        out["top_decile_precision"] = round(float(yv[top].mean()), 4)
        out["top_decile_lift"] = round(float(yv[top].mean() / base), 3)
        # calibration: predicted vs observed by decile
        bins = pd.qcut(pd.Series(pv), 10, duplicates="drop")
        cal = pd.DataFrame({"p": pv, "y": yv, "b": bins}).groupby("b", observed=True).agg(n=("y", "size"), mean_pred=("p", "mean"), observed=("y", "mean"))
        out["calibration"] = [{"n": int(r.n), "mean_pred": round(float(r.mean_pred), 4), "observed": round(float(r.observed), 4)} for r in cal.itertuples()]
        # does the score rank the P&L of the plan the UI shows?
        r = meta["plan_r"].to_numpy()
        q = pd.qcut(pd.Series(pv).rank(method="first"), 5, labels=False).to_numpy()
        out["plan_r_by_score_quintile"] = [round(float(r[q == k].mean()), 4) for k in range(5)]
        out["auc_vs_plan_profit"] = round(roc_auc_score((r > 0).astype(int), pv), 4)
        out["plan_r_mean_all"] = round(float(r.mean()), 4)
        for name, d in (("bullish", 1), ("bearish", -1)):
            m = (meta["direction"] == d).to_numpy()
            out[f"auc_vs_plan_profit_{name}"] = round(roc_auc_score((r[m] > 0).astype(int), pv[m]), 4)
        return out

    def walk_forward(self, X, y, meta):
        dates = meta["date"]
        u = np.sort(dates.unique())
        edges = np.array_split(u, WF_FOLDS + 2)   # first two chunks are the minimum training history
        rows = []
        for k in range(2, WF_FOLDS + 2):
            t0, t1 = edges[k][0], edges[k][-1]
            tr = dates < pd.Timestamp(t0) - pd.Timedelta(days=EMBARGO_DAYS)
            te = (dates >= t0) & (dates <= t1)
            if tr.sum() < 2000 or te.sum() < 500 or len(np.unique(y[te])) < 2:
                continue
            m, _ = self._train_with_inner_validation(X[tr].reset_index(drop=True), y[tr].reset_index(drop=True), dates[tr].reset_index(drop=True))
            p = m.predict_proba(X[te])[:, 1]
            rows.append({"from": str(pd.Timestamp(t0).date()), "to": str(pd.Timestamp(t1).date()), "n_test": int(te.sum()),
                         "base_rate": round(float(y[te].mean()), 4), "auc": round(roc_auc_score(y[te], p), 4)})
        return rows

    def gate(self, holdout, folds):
        checks = {
            "holdout_auc": (holdout["auc"], GATE["min_holdout_auc"], holdout["auc"] >= GATE["min_holdout_auc"]),
            "auc_ci_low": (holdout["auc_ci95"][0], GATE["min_auc_ci_low"], holdout["auc_ci95"][0] > GATE["min_auc_ci_low"]),
            "over_direction_baseline": (round(holdout["auc"] - holdout["direction_baseline_auc"], 4), GATE["min_auc_over_direction_baseline"],
                                        holdout["auc"] - holdout["direction_baseline_auc"] >= GATE["min_auc_over_direction_baseline"]),
            "walk_forward_share_auc_gt_0.5": (round(float(np.mean([f["auc"] > 0.5 for f in folds])) if folds else 0.0, 3), GATE["min_wf_share_above_half"],
                                              bool(folds) and np.mean([f["auc"] > 0.5 for f in folds]) >= GATE["min_wf_share_above_half"]),
            "top_decile_lift": (holdout["top_decile_lift"], GATE["min_top_decile_lift"], holdout["top_decile_lift"] >= GATE["min_top_decile_lift"]),
            "plan_profit_auc_within_direction": (min(holdout["auc_vs_plan_profit_bullish"], holdout["auc_vs_plan_profit_bearish"]),
                                                 GATE["min_plan_profit_auc_within_direction"],
                                                 min(holdout["auc_vs_plan_profit_bullish"], holdout["auc_vs_plan_profit_bearish"]) >= GATE["min_plan_profit_auc_within_direction"]),
        }
        return {k: {"value": v[0], "required": v[1], "pass": bool(v[2])} for k, v in checks.items()}, all(v[2] for v in checks.values())

    # ------------------------------------------------------------------ pipeline
    def run(self, promote=True, min_date=None):
        print(f"Target: {self.target} -- {TARGETS[self.target][1]}")
        X, y, meta = self.load_dataset(min_date)
        dates = meta["date"]
        print(f"Dataset: {len(X)} mature samples, {dates.min().date()} -> {dates.max().date()}, base rate {y.mean():.3f}")

        cut = dates.quantile(1 - HOLDOUT_FRAC, interpolation="lower")
        tr = (dates < cut - pd.Timedelta(days=EMBARGO_DAYS)).to_numpy()
        te = (dates >= cut).to_numpy()
        print(f"Train {dates[tr].min().date()} -> {dates[tr].max().date()} ({tr.sum()}) | embargo {EMBARGO_DAYS}d | "
              f"holdout {dates[te].min().date()} -> {dates[te].max().date()} ({te.sum()})")

        Xtr, ytr, dtr = X[tr].reset_index(drop=True), y[tr].reset_index(drop=True), dates[tr].reset_index(drop=True)
        model, n_trees = self._train_with_inner_validation(Xtr, ytr, dtr)
        p = model.predict_proba(X[te])[:, 1]
        dir_rate = meta[tr].groupby("direction")["target_binary" if self.target == "momentum" else "plan_profit"].mean()
        dir_base = meta.loc[te, "direction"].map(dir_rate).to_numpy()
        holdout = self.evaluate(y[te].reset_index(drop=True), p, meta[te].reset_index(drop=True), dir_base)
        folds = self.walk_forward(X, y, meta)
        gate, passed = self.gate(holdout, folds)

        imp = sorted(zip(self.feature_names, model.feature_importances_), key=lambda t: -t[1])[:12]
        report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "feature_set_version": pf.FEATURE_SET_VERSION,
                  "target": self.target, "target_definition": TARGETS[self.target][1],
                  "train_range": [str(dates[tr].min().date()), str(dates[tr].max().date())], "n_train": int(tr.sum()),
                  "holdout_range": [str(dates[te].min().date()), str(dates[te].max().date())], "embargo_days": EMBARGO_DAYS,
                  "trees": n_trees, "params": XGB_PARAMS, "excluded_features": self.exclude, "holdout": holdout, "walk_forward": folds, "gate": gate,
                  "promoted": False, "top_features": [{"feature": f, "importance": round(float(v), 4)} for f, v in imp]}
        self._print(report)

        if promote and passed:
            final, _ = self._train_with_inner_validation(X, y, dates)   # refit on ALL mature data, same protocol
            self.model = final
            path = self.save_model(report)
            report["promoted"] = True
            report["model_file"] = os.path.basename(path)
            self.register_model(report, dates.min().date(), dates.max().date(), path)
            print(f"\nPROMOTED -> {os.path.basename(path)} (honest holdout AUC {holdout['auc']}, CI {holdout['auc_ci95']})")
        else:
            why = "gate failed: " + ", ".join(k for k, v in gate.items() if not v["pass"]) if not passed else "--no-promote"
            print(f"\nNOT PROMOTED ({why}). The live screener keeps whatever model (if any) it already had.")
        cand = os.path.join(HERE, "candidates")
        os.makedirs(cand, exist_ok=True)
        rp = os.path.join(cand, f"report_{self.target}_{datetime.now():%Y%m%d_%H%M}.json")
        with open(rp, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Report: {rp}")
        return report

    @staticmethod
    def _print(r):
        h = r["holdout"]
        print(f"\nHOLDOUT (untouched, {r['holdout_range'][0]} -> {r['holdout_range'][1]}, n={h['n']}, base rate {h['base_rate']})")
        print(f"  AUC {h['auc']}  95% CI {h['auc_ci95']}  | direction-only baseline AUC {h['direction_baseline_auc']}")
        print(f"  AUC bullish {h['auc_bullish']} (n={h['n_bullish']}) | bearish {h['auc_bearish']} (n={h['n_bearish']})")
        print(f"  Brier {h['brier']} vs base-rate Brier {h['brier_base_rate']} | top-decile precision {h['top_decile_precision']} (lift {h['top_decile_lift']}x)")
        print(f"  AUC vs plan profit within direction: bullish {h['auc_vs_plan_profit_bullish']} | bearish {h['auc_vs_plan_profit_bearish']}")
        print(f"  AUC vs plan profit (R>0): {h['auc_vs_plan_profit']} | mean plan R by score quintile (low->high): {h['plan_r_by_score_quintile']} (all {h['plan_r_mean_all']})")
        print("  Calibration (pred -> observed): " + ", ".join(f"{c['mean_pred']:.2f}->{c['observed']:.2f}" for c in h["calibration"]))
        print("WALK-FORWARD: " + " | ".join(f"{f['from'][:7]}..{f['to'][:7]} AUC {f['auc']}" for f in r["walk_forward"]))
        print("GATE: " + ", ".join(f"{k} {v['value']} (need {v['required']}) {'OK' if v['pass'] else 'FAIL'}" for k, v in r["gate"].items()))
        print("Top features: " + ", ".join(f"{t['feature']} {t['importance']}" for t in r["top_features"][:8]))

    # ------------------------------------------------------------------ persistence
    def save_model(self, report):
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        self.model_version = f"momentum_predictor_v{ts}"
        base = os.path.join(HERE, self.model_version)
        joblib.dump(self.model, base + ".joblib")
        with open(base + "_features.json", "w") as f:
            json.dump(self.feature_names, f)
        with open(base + "_meta.json", "w") as f:   # enhancer only loads models that carry this contract
            json.dump({"feature_set_version": pf.FEATURE_SET_VERSION, "target": self.target,
                       "target_definition": TARGETS[self.target][1], "trained_at": datetime.now().isoformat(timespec="seconds"),
                       "holdout": report["holdout"], "walk_forward": report["walk_forward"], "gate": report["gate"]}, f, indent=2, default=str)
        return base + ".joblib"

    def register_model(self, report, start, end, path):
        """Record the run in ml_models with HONEST holdout metrics (accuracy/precision/recall at 0.5 are on the
        holdout too; AUC is the untouched-holdout AUC)."""
        try:
            h = report["holdout"]
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor()
            cur.execute("ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS evaluation JSONB")
            cur.execute("ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS feature_set_version VARCHAR(20)")
            cur.execute("ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS target VARCHAR(30)")
            cur.execute("UPDATE ml_models SET is_active = FALSE WHERE is_active = TRUE")
            cur.execute("""INSERT INTO ml_models (model_name, version, model_type, training_start_date, training_end_date,
                    training_samples, auc_score, model_file_path, scaler_file_path, feature_names, is_active, deployment_date,
                    evaluation, feature_set_version, target)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s)""",
                        ("momentum_predictor", self.model_version, "xgboost", start, end, report["n_train"], h["auc"], path, None,
                         json.dumps(self.feature_names), datetime.now(), json.dumps(report, default=str), pf.FEATURE_SET_VERSION, self.target))
            conn.commit()
            conn.close()
            print(f"Registered {self.model_version} in ml_models")
        except Exception as e:
            print(f"WARNING: could not register model in ml_models: {e} (model file is saved)")


def main():
    ap = argparse.ArgumentParser(description="Train/evaluate the breakout model with an honest protocol")
    ap.add_argument("--target", choices=list(TARGETS), default="momentum")
    ap.add_argument("--no-promote", action="store_true", help="evaluate and write a report only")
    ap.add_argument("--min-date", default=None, help="only use samples on/after this date")
    ap.add_argument("--exclude", nargs="*", default=[], help="feature names to drop (ablation)")
    a = ap.parse_args()
    MomentumBreakoutPredictor(a.target, a.exclude).run(promote=not a.no_promote, min_date=a.min_date)


if __name__ == "__main__":
    main()
