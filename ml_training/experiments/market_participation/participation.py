"""breakout_participation_v1 -- how many stocks broke out today, as explicit counts, strictly point in time. PURE: frames in, frame out; no database, no network, no clock.

WHAT IT MEASURES
For each session t: of the stocks that COULD have signalled (the eligible denominator), how many produced a Donchian breakout (long / short), and how many of those started a new
episode (not a repeat of the same symbol's recent signal). It is a descriptive count of market-wide breakout participation, the missing sibling of breadth_v1 (which counts
stocks above averages / at highs, never stocks that broke their channel). It is not a signal, a score or a feature of any strategy, and nothing here assumes it predicts returns.

POINT IN TIME
Every value for session t is a function of bars up to and including t. This is stricter than the research signal set (`bw_harness.features.detect_signals` / `ml_breakout_dataset_v2`),
which also drops a signal when a price discontinuity falls in the NEXT 20 bars and requires the symbol to have 80+ bars in total: both use the future. Here the discontinuity exclusion
looks backwards only (a discontinuity in the previous 253 bars) and no total-length condition exists. Each session also reports how many of its signals the research rule would have
dropped (`*_dropped_by_research_rule`), so the size of that lookahead is visible and a test pins that the series is causal.

PROVENANCE
Computed from the stored, split/dividend-ADJUSTED price history, so it is REVISED_PRICE_DERIVED (a later vendor revision can change a past value). It is never "observed": only a
snapshot captured at the time could be. The denominator is the surviving universe (delisted symbols are absent), which is stated on every row.

DEFINITIONS (fixed, versioned; nothing is fitted)
  eligible(symbol, t)  the symbol has a bar on t, at least 60 bars of history, 20-day average dollar volume >= 1,000,000, and no price discontinuity in the previous 253 bars
  long / short         the live screener's rule: close crosses the previous bar's 20-bar channel while the previous close was inside it
  new episode          per (symbol, direction), a signal at least 120 bars after the previous KEPT signal (same rule as the Big Winner harness)
  pct                  100 * n / n_eligible, withheld (None) when n_eligible < MIN_ELIGIBLE (state `insufficient_eligible`)
A stock that cannot be evaluated leaves the denominator; it is missing, never "no breakout".
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
from typing import Dict, Mapping, Optional

import numpy as np
import pandas as pd

_EXP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT = os.path.abspath(os.path.join(_EXP, "..", ".."))
for _p in (_EXP, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from ml_training.features import price_features as pf  # noqa: E402
from bw_harness.episodes import assign_episodes  # noqa: E402
from bw_harness.features import MIN_BAR_INDEX, clean_prices  # noqa: E402

VERSION = "breakout_participation_v1"
PROVENANCE = "REVISED_PRICE_DERIVED"
UNIVERSE_NOTE = "surviving universe only (delisted symbols are absent)"
MIN_ELIGIBLE = 1_000
EPISODE_GAP = 120
OK, INSUFFICIENT = "ok", "insufficient_eligible"

COLUMNS = ["session_date", "n_eligible", "n_long", "n_short", "n_long_new_episode", "n_short_new_episode", "pct_long", "pct_short",
           "n_long_dropped_by_research_rule", "n_short_dropped_by_research_rule", "state", "version", "provenance"]


CAUSAL_COLUMNS = ["session_date", "n_eligible", "n_long", "n_short", "n_long_new_episode", "n_short_new_episode", "pct_long", "pct_short", "state", "version", "provenance"]
AUDIT_COLUMNS = ["n_long_dropped_by_research_rule", "n_short_dropped_by_research_rule"]      # NOT point in time: they use bars after the session and exist only to size the lookahead of the research dataset


def valid(df: pd.DataFrame) -> pd.DataFrame:
    """The sessions whose participation statistics may be used: state `ok` only. `insufficient_eligible` rows (a holiday with a stray bar, a partial load) carry counts for audit but no percentage and are not statistics."""
    return df[df["state"] == OK].reset_index(drop=True)


def _symbol_rows(sym: str, px: pd.DataFrame) -> Optional[pd.DataFrame]:
    px = clean_prices(px)
    n = len(px)
    if n <= MIN_BAR_INDEX:
        return None
    disc = pf.find_discontinuities(px)
    ind = pf.compute_indicators(px)
    d = pf.detect_breakouts(ind).astype(int)
    liquid = (ind["dollar_vol_20"] >= pf.MIN_DOLLAR_VOLUME_20).to_numpy()
    pos = np.arange(n)
    past_bad = pf.contaminated_mask(n, disc["pos"], lookback=pf.LOOKBACK_BARS, forward=0)           # looks backwards only
    eligible = liquid & ~past_bad & (pos >= MIN_BAR_INDEX)
    research_bad = pf.contaminated_mask(n, disc["pos"])                                              # the research rule: also 20 bars ahead
    research_ok = ~research_bad & (n >= MIN_BAR_INDEX + pf.PLAN_HORIZON)                           # ... and needs the total length
    sel = np.flatnonzero(eligible)
    if len(sel) == 0:
        return None
    return pd.DataFrame({"symbol": sym, "bar": sel, "date": pd.to_datetime(ind["date"]).to_numpy()[sel], "direction": d[sel], "research_ok": research_ok[sel]})


def session_table(prices: Mapping[str, pd.DataFrame], start: Optional[str] = None, min_eligible: int = MIN_ELIGIBLE) -> pd.DataFrame:
    """One row per session on which at least one stock was eligible. `start` only trims the output; episodes are always chained from each symbol's first bar."""
    parts = []
    for sym in sorted(prices):
        r = _symbol_rows(sym, prices[sym])
        if r is not None:
            parts.append(r)
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    rows = pd.concat(parts, ignore_index=True)
    sig = rows[rows["direction"] != 0].copy()
    if len(sig):
        _, first = assign_episodes(sig["symbol"].to_numpy(), sig["direction"].to_numpy(), sig["bar"].to_numpy(), EPISODE_GAP)
        sig["new_episode"] = first
    else:
        sig["new_episode"] = pd.Series(dtype=bool)
    elig = rows.groupby("date").size().rename("n_eligible")
    g = pd.DataFrame(index=elig.index)
    g["n_eligible"] = elig
    for name, dirn in (("long", 1), ("short", -1)):
        s = sig[sig["direction"] == dirn]
        g[f"n_{name}"] = s.groupby("date").size().reindex(g.index, fill_value=0)
        g[f"n_{name}_new_episode"] = s[s["new_episode"]].groupby("date").size().reindex(g.index, fill_value=0)
        g[f"n_{name}_dropped_by_research_rule"] = s[~s["research_ok"]].groupby("date").size().reindex(g.index, fill_value=0)
    ok = g["n_eligible"] >= min_eligible
    g["pct_long"] = np.where(ok, (100.0 * g["n_long"] / g["n_eligible"]).round(4), np.nan)
    g["pct_short"] = np.where(ok, (100.0 * g["n_short"] / g["n_eligible"]).round(4), np.nan)
    g["state"] = np.where(ok, OK, INSUFFICIENT)
    g["version"] = VERSION
    g["provenance"] = PROVENANCE
    g.index.name = "session_date"
    out = g.reset_index()
    if start is not None:
        out = out[out["session_date"] >= pd.Timestamp(start)].reset_index(drop=True)
    return out[COLUMNS]


def table_hash(df: pd.DataFrame) -> str:
    """sha256 of the canonical CSV text of the table (same rows and values => same hash, independent of run time)."""
    buf = io.StringIO()
    df.to_csv(buf, index=False, float_format="%.10g", lineterminator="\n", date_format="%Y-%m-%d")
    return hashlib.sha256(buf.getvalue().encode("utf-8")).hexdigest()


def summary(df: pd.DataFrame) -> Dict[str, object]:
    ok = valid(df)
    if len(ok) == 0:
        return {"sessions": int(len(df)), "sessions_ok": 0}
    q = lambda s: {k: round(float(v), 4) for k, v in zip(("min", "p10", "p50", "p90", "p99", "max"), np.percentile(s, [0, 10, 50, 90, 99, 100]))}
    return {"sessions": int(len(df)), "sessions_ok": int(len(ok)), "first": str(df["session_date"].min().date()), "last": str(df["session_date"].max().date()),
            "pct_long": q(ok["pct_long"]), "pct_short": q(ok["pct_short"]),
            "share_long_signals_new_episode": round(float(ok["n_long_new_episode"].sum() / max(1, ok["n_long"].sum())), 4),
            "share_short_signals_new_episode": round(float(ok["n_short_new_episode"].sum() / max(1, ok["n_short"].sum())), 4),
            "long_signals": int(ok["n_long"].sum()), "long_dropped_by_research_rule": int(ok["n_long_dropped_by_research_rule"].sum()),
            "short_signals": int(ok["n_short"].sum()), "short_dropped_by_research_rule": int(ok["n_short_dropped_by_research_rule"].sum())}
