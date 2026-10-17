"""Provenance of every dataset column. Four classes, no others:

  OBSERVED_PIT       recorded by First Light itself at the time, database-stamped (forward tables since 2026-10-09; `daily_fundamentals` snapshots from 2023-07-11, as of each fetch date)
  RECONSTRUCTED      built afterwards from a panel or a mapping that did not exist at the time (a survivor-only index, today's sector applied backwards, a static membership)
  REVISED_PRICE_DERIVED  computed from bars up to the signal bar (causal), but from today's vendor-revised price history, so it need not equal what was visible on the day
  UNAVAILABLE        no source exists (the column is present only to say so)

A column with no entry here is a defect: `check_dataset` fails. Labels and hindsight columns are not features and carry the class `LABEL_HINDSIGHT`; identifiers carry `KEY`."""
from __future__ import annotations

from typing import Dict, Iterable, List

OBSERVED_PIT = "OBSERVED_PIT"
RECONSTRUCTED = "RECONSTRUCTED"
REVISED_PRICE_DERIVED = "REVISED_PRICE_DERIVED"
UNAVAILABLE = "UNAVAILABLE"
LABEL_HINDSIGHT = "LABEL_HINDSIGHT"
KEY = "KEY"
CLASSES = (OBSERVED_PIT, RECONSTRUCTED, REVISED_PRICE_DERIVED, UNAVAILABLE, LABEL_HINDSIGHT, KEY)
FEATURE_CLASSES = (OBSERVED_PIT, RECONSTRUCTED, REVISED_PRICE_DERIVED, UNAVAILABLE)

PRICE_FEATURES = ["breakout_dist_atr", "chan_width_pct", "chan_squeeze_pctile", "atr_pct", "rsi_14", "vol_ratio_10", "vol_ratio_50", "log_dollar_vol_20", "ret_5d", "ret_20d", "ret_60d",
                  "sma10_vs_20", "dist_sma50_pct", "dist_sma200_pct", "macd_norm", "bollinger_pos", "gap_pct", "bar_range_atr", "close_location", "pct_from_52w_high", "pct_from_52w_low"]

# column -> (class, source / rule). Static classes; per-row refinements are separate `*_provenance` columns.
STATIC: Dict[str, tuple] = {
    **{f: (REVISED_PRICE_DERIVED, "stock_prices bars <= signal bar (price_features.build_breakout_features), today's vendor vintage") for f in PRICE_FEATURES},
    "dollar_vol_20": (REVISED_PRICE_DERIVED, "20-day mean of close x volume, bars <= signal bar"),
    "atr14": (REVISED_PRICE_DERIVED, "Wilder ATR14 at the signal bar"),
    "unit_r": (REVISED_PRICE_DERIVED, "1R = 2 x ATR14 of the signal bar (a decision-time number)"),
    "sigma60": (REVISED_PRICE_DERIVED, "std of daily log returns over the 60 bars ending at the signal bar; used only to scale the vol-neutral label"),
    "entry_px": (LABEL_HINDSIGHT, "open of the bar after the signal: not known at the decision"),
    "entry_date": (LABEL_HINDSIGHT, "date of the bar after the signal"),
    "window_end_date": (LABEL_HINDSIGHT, "date of the last bar of the primary-horizon label window"),
    "rs60": (RECONSTRUCTED, "stock 60-bar return minus the equal-weight index return, index built from the surviving universe"),
    "rs120": (RECONSTRUCTED, "same over 120 bars"),
    "rs60_pct": (RECONSTRUCTED, "cross-sectional percentile of rs60 among liquid symbols on the signal date"),
    "rs120_pct": (RECONSTRUCTED, "cross-sectional percentile of rs120 among liquid symbols on the signal date"),
    "index_above_200": (RECONSTRUCTED, "equal-weight survivor index above its 200-bar average"),
    "breadth_50": (RECONSTRUCTED, "share of liquid surviving symbols above their 50-bar average"),
    "sector": (RECONSTRUCTED, "latest known sector applied backwards (see sector_provenance for rows with a dated snapshot)"),
    "sector_provenance": (KEY, "OBSERVED_PIT where a daily_fundamentals snapshot on or before the signal date supplied the sector, else RECONSTRUCTED"),
    "sector_rs60_rank": (RECONSTRUCTED, "rank of the sector's mean 60-bar relative strength among 11 sectors; membership as of the nearest dated snapshot (static before 2023-07-11)"),
    "group_breakouts_10": (RECONSTRUCTED, "other same-sector same-direction breakout signals in the previous 10 sessions"),
    "industry": (UNAVAILABLE, "no point-in-time industry before 2023-07-11 and not used"),
    "market_cap": (UNAVAILABLE, "no point-in-time history before 2023-07-11 and not used"),
    "catalyst": (UNAVAILABLE, "no observed catalyst feed yet"),
    "delisting_flag": (UNAVAILABLE, "no listing or delisting history exists (companies table is empty)"),
}

KEYS = ["symbol", "signal_date", "direction", "bar_index", "episode_id", "is_episode_first", "segment", "symbol_first_bar_year", "cohort", "year", "date_index", "month",
        "history_ok", "eligible", "h1_treated", "h1_control", "h2_treated", "h2_control", "h3_treated", "h3_control", "h4_treated", "h4_control", "h5_treated", "h5_control",
        "h0_treated", "h0_control", "atr_decile", "dv_tercile", "ret60_tercile"]


def is_label(col: str) -> bool:
    return col.startswith(("status_", "mfe_r_", "mfe_pct_", "terminal_r_", "ge3r_", "ge5r_", "ge10r_", "ret_label_", "vn_z_", "vn_label_", "persist_", "tier_", "realised_"))


def classify(col: str) -> tuple:
    if col in STATIC:
        return STATIC[col]
    if is_label(col):
        return (LABEL_HINDSIGHT, "needs bars after the decision; never a feature")
    if col in KEYS:
        return (KEY, "identifier or derived flag")
    raise KeyError(col)


def provenance_table(columns: Iterable[str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for c in columns:
        cls, src = classify(c)
        out[c] = {"class": cls, "source": src}
    return out


def check_dataset(columns: Iterable[str]) -> List[str]:
    """Columns that have no provenance entry (must be empty)."""
    missing = []
    for c in columns:
        try:
            classify(c)
        except KeyError:
            missing.append(c)
    return missing
