"""Year-by-year stability of parameter-free rankers on the real dataset (no fitting, so every year is out of sample for the ranker). Read-only; writes /out/ranker_stability.json.
Candidates are declared up front (7); the report states how many were tried so the best one is read with that multiple-comparison discount."""
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")
from ml_training.features import price_features as pf  # noqa: E402

conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                        options="-c default_transaction_read_only=on")
df = pd.read_sql("SELECT symbol, date, direction, features, plan_r FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s AND plan_r IS NOT NULL", conn, params=(pf.FEATURE_SET_VERSION,))
df["date"] = pd.to_datetime(df["date"])
f = pd.DataFrame(df.pop("features").tolist(), index=df.index).reindex(columns=pf.FEATURE_NAMES).astype("float32")
dirn = df["direction"].to_numpy()
cands = {   # score: higher = predicted better; sign conventions stated in the name
    "less_extended_vs_52w_high_in_direction": -(f["pct_from_52w_high"] * np.where(dirn == 1, 1, -1)),
    "less_extended_beyond_sma50_in_direction": -(f["dist_sma50_pct"] * dirn),
    "smaller_breakout_distance_atr": -f["breakout_dist_atr"],
    "smaller_bar_range_atr": -f["bar_range_atr"],
    "tighter_channel_squeeze": -f["chan_squeeze_pctile"],
    "lower_atr_pct": -f["atr_pct"],
    "weaker_ret_20d_in_direction": -(f["ret_20d"] * dirn),
}


def quintile_spread(score, y):
    q = pd.qcut(pd.Series(score).rank(method="first"), 5, labels=False).to_numpy()
    return float(y[q == 4].mean() - y[q == 0].mean()), float(y[q == 4].mean()), float(y[q == 0].mean())


out = {"n_candidates_tried": len(cands), "definition": "score high = predicted better trade; spread = mean plan_r of the top score quintile minus the bottom quintile, computed inside each calendar year (and inside each direction)"}
for name, sc in cands.items():
    res = {"by_year": {}, "by_direction": {}}
    s = sc.to_numpy()
    y = df["plan_r"].to_numpy()
    for yr, idx in df.groupby(df.date.dt.year).indices.items():
        sp, top, bot = quintile_spread(s[idx], y[idx])
        res["by_year"][int(yr)] = {"spread": round(sp, 4), "top_q": round(top, 4), "bottom_q": round(bot, 4), "spearman": round(float(pd.Series(s[idx]).corr(pd.Series(y[idx]), method="spearman")), 4)}
    for d_, nm in ((1, "bullish"), (-1, "bearish")):
        m = dirn == d_
        sp, top, bot = quintile_spread(s[m], y[m])
        res["by_direction"][nm] = {"spread": round(sp, 4), "top_q": round(top, 4), "bottom_q": round(bot, 4)}
        res["by_direction"][nm]["years_positive_spread"] = int(sum(1 for yr, idx in df[m].groupby(df[m].date.dt.year).indices.items() if quintile_spread(s[m][idx], y[m][idx])[0] > 0))
    yrs = res["by_year"]
    res["years_with_positive_spread"] = int(sum(1 for v in yrs.values() if v["spread"] > 0))
    res["n_years"] = len(yrs)
    res["mean_spread"] = round(float(np.mean([v["spread"] for v in yrs.values()])), 4)
    res["spearman_all"] = round(float(pd.Series(s).corr(pd.Series(y), method="spearman")), 4)
    out[name] = res
    print(name, "years+", res["years_with_positive_spread"], "/", res["n_years"], "mean spread", res["mean_spread"], "spearman", res["spearman_all"], flush=True)
json.dump(out, open("/out/ranker_stability.json", "w"), indent=1)
print("WROTE ranker_stability.json")
