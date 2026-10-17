"""Data-quality and bias report computed from the dataset itself (no database): counts, maturity, base rates, overlap, clustering, provenance mix, null rates. Descriptive only."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from .config import Config
from .provenance import CLASSES, classify


def report(df: pd.DataFrame, cfg: Config, reconciliation: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"signal_reconciliation": reconciliation}
    out["rows"] = int(len(df))
    out["eligible"] = int(df["eligible"].sum())
    out["by_direction"] = {("long" if d == 1 else "short"): int((df.direction == d).sum()) for d in (1, -1)}
    out["segments"] = {s: {"signals": int((df.segment == s).sum()), "independent_episodes": int(((df.segment == s) & df.is_episode_first & df.eligible).sum())} for s in sorted(df.segment.unique())}
    out["label_status"] = {f"h{H}": {k: int(v) for k, v in df[f"status_{H}"].value_counts().items()} for H in cfg.horizons}
    out["label_rule"] = "immature and discontinuity rows have NaN labels and are excluded from every rate; none is counted as a negative"
    ok = df[(df["status_120"] == "ok") & df["eligible"]]
    first = ok[ok["is_episode_first"]]
    out["independent_episodes_with_primary_label"] = {"long": int((first.direction == 1).sum()), "short": int((first.direction == -1).sum())}
    base: Dict[str, Any] = {}
    for name, g in (("all_signals", ok), ("episode_first", first)):
        for d, side in ((1, "long"), (-1, "short")):
            gg = g[g.direction == d]
            base[f"{name}_{side}"] = {"n": int(len(gg)), **{f"ge{int(t)}r_120": round(float(gg[f"ge{int(t)}r_120"].mean()), 4) for t in cfg.tiers_r}, "return_label_120": round(float(gg["ret_label_120"].mean()), 4),
                                      "vol_neutral_label_120": round(float(gg["vn_label_120"].mean()), 4)}
    out["base_rates_120"] = base
    out["bw5_120_by_year_direction_episode_first"] = {f"{y}_{'long' if d == 1 else 'short'}": {"n": int(len(g)), "rate": round(float(g.ge5r_120.mean()), 4)} for (y, d), g in first.groupby(["year", "direction"])}
    out["bw5_by_horizon_long_all_signals"] = {f"h{H}": {"labelled": int((df[f"status_{H}"] == "ok").sum()), "rate": round(float(df.loc[(df[f"status_{H}"] == "ok") & (df.direction == 1) & df.eligible, f"ge5r_{H}"].mean()), 4)} for H in cfg.horizons}
    # mfe vs terminal
    w = ok[(ok.direction == 1) & (ok.ge5r_120 == 1)]
    out["long_bw5_winners_mfe_vs_terminal"] = {"n": int(len(w)), "median_mfe_r": round(float(w.mfe_r_120.median()), 2), "median_terminal_r": round(float(w.terminal_r_120.median()), 2),
                                               "share_terminal_below_half_of_mfe": round(float((w.terminal_r_120 < 0.5 * w.mfe_r_120).mean()), 4), "share_terminal_negative": round(float((w.terminal_r_120 < 0).mean()), 4)} if len(w) else {}
    # overlap and clustering
    s = df.sort_values(["symbol", "direction", "bar_index"])
    gap = s.groupby(["symbol", "direction"])["bar_index"].diff()
    out["overlap"] = {"share_within_10_bars_of_previous_same_symbol_direction": round(float((gap <= 10).mean()), 4), "share_within_120": round(float((gap <= 120).mean()), 4),
                      "signals_per_episode_mean": round(float(len(df) / max(1, df.is_episode_first.sum())), 2)}
    per_date = df.groupby("date_index").size()
    out["date_clustering"] = {"dates": int(len(per_date)), "mean_signals_per_date": round(float(per_date.mean()), 1), "max_signals_per_date": int(per_date.max())}
    m = first[first.direction == 1].groupby("month").agg(n=("ge5r_120", "size"), w=("ge5r_120", "sum"))
    if len(m):
        k = max(1, int(round(len(m) * 0.1)))
        mm = m.sort_values("w", ascending=False)
        out["month_concentration_long_bw5_episode_first"] = {"months": int(len(m)), "top10pct_months_share_of_winners": round(float(mm.w.head(k).sum() / max(1.0, mm.w.sum())), 4),
                                                             "top10pct_months_share_of_signals": round(float(mm.n.head(k).sum() / mm.n.sum()), 4),
                                                             "monthly_rate_min_median_max": [round(float((m.w / m.n).min()), 3), round(float((m.w / m.n).median()), 3), round(float((m.w / m.n).max()), 3)]}
    # features
    feats = [c for c in df.columns if classify(c)[0] in ("REVISED_PRICE_DERIVED", "RECONSTRUCTED")]
    out["feature_null_rates_eligible"] = {c: round(float(df.loc[df.eligible, c].isna().mean()), 4) for c in feats if c in df}
    out["provenance_class_counts"] = {c: sum(1 for col in df.columns if classify(col)[0] == c) for c in CLASSES}
    out["sector_provenance_share_eligible"] = {k: round(float(v), 4) for k, v in df.loc[df.eligible, "sector_provenance"].value_counts(normalize=True).items()}
    out["first_date_with_observed_sector"] = str(df.loc[df.sector_provenance == "OBSERVED_PIT", "signal_date"].min().date()) if (df.sector_provenance == "OBSERVED_PIT").any() else None
    out["realised_outcome_coverage"] = {c: int(df[c].notna().sum()) for c in df.columns if c.startswith("realised_")}
    return out
