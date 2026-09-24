# mechanism/alerts/board.py
"""
The channel's MOMENTUM BOARD: of the stocks the daily lists carried in the previous few sessions, who moved most today.

Pure pandas (no database, no network): the loader lives in send_channel_posts.py, the wording in channel_content.post_board, the picture in
channel_cards.render_board_card.

What it says, and what it deliberately does not:
  * pool      every stock that was in ANY of the channel's lists (either group) on one of the previous BOARD_SESSIONS stored sessions. Today's own
              listings are left out: they are the lists themselves, and ranking them by today's gain would just repeat "Top gainers".
  * ranking   today's % change from the previous close (a fact about one day). Places are given only to stocks that closed HIGHER; if fewer than
              three did, fewer places are shown - a "gain board" never crowns a loser.
  * context   the counts of the WHOLE pool (how many closed higher / lower, how many closed in the top of their day's range, how many closed above
              their prior 20-day high), so the top three are never shown without the denominator. This is what keeps a "best of the pool" list
              from reading as a claim about the lists.
  * no claim  nothing here is a forecast or a track record: consecutive days of the same stock overlap, and earlier lists say nothing about later ones.
  * honesty   a stock whose close-to-close move (today, or anywhere in the chart window) looks like a split / price adjustment (price_guard), or that has
              no bar today or yesterday, is left out and counted as not measurable - never repaired, never guessed.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from alerts.market_stats import Wide
from alerts.price_guard import is_split_like

BOARD_SESSIONS = 5          # previous stored sessions whose lists form the pool
CHART_SESSIONS = 10         # closes drawn for the leader's chart (today included)
PODIUM = 3                  # places on the picture
MORE_RANKS = 8              # ranks listed in text (podium + a collapsed few more)
AT_HIGH_POS = 0.90          # "closed at the top of its day's range": (close - low) / (high - low) >= 0.90
GROUPS = ("breakout", "near_breakout")


def _finite(*xs) -> bool:
    return all(x is not None and np.isfinite(x) for x in xs)


def _window_is_clean(closes: pd.Series) -> bool:
    """False when any close-to-close step in the chart window is split-shaped or a close is missing / not positive (the chart would be a lie)."""
    v = closes.to_numpy(dtype=float)
    if len(v) < 2 or not np.isfinite(v).all() or (v <= 0).any():
        return False
    return not any(is_split_like(float(b / a)) for a, b in zip(v[:-1], v[1:]))


def _bar(o, h, l, c):
    """One candle as (open, high, low, close), or None when the bar cannot be drawn honestly: a missing value, a non-positive price, or a bar
    whose high / low do not contain its own open and close (an inconsistent vendor bar). It is skipped, never repaired."""
    vals = (o, h, l, c)
    if not all(x is not None and np.isfinite(x) and x > 0 for x in vals):
        return None
    if h < max(o, c) or l > min(o, c):
        return None
    return (float(o), float(h), float(l), float(c))


def compute(listed: pd.DataFrame, w: Wide, session, top_n: int = MORE_RANKS) -> Optional[Dict]:
    """listed: columns session_date, symbol, category - one row per stock per PREVIOUS session it was in a list. w: date x symbol prices whose last
    date is `session`. Returns the board dict, or None when there is nothing honest to show (no pool, no bar for the session, nobody closed higher)."""
    ts = pd.Timestamp(session)
    if not len(listed) or len(w.close.index) < CHART_SESSIONS or w.close.index[-1] != ts:
        return None
    dates = w.close.index
    chart_dates = dates[-CHART_SESSIONS:]
    listed = listed[pd.to_datetime(listed["session_date"]) < ts]
    if not len(listed):
        return None
    window = sorted(pd.to_datetime(listed["session_date"]).dt.date.unique())

    hi20 = w.high.iloc[-21:-1].max()
    vol_med = w.volume.iloc[-51:-1].median()
    rows: List[Dict] = []
    excluded = 0
    for sym, sub in listed.groupby("symbol"):
        if sym not in w.close.columns:
            excluded += 1
            continue
        closes = w.close[sym].iloc[-CHART_SESSIONS:]
        c, pc = float(w.close[sym].iloc[-1]), float(w.close[sym].iloc[-2])
        hi, lo = float(w.high[sym].iloc[-1]), float(w.low[sym].iloc[-1])
        if not (_finite(c, pc) and c > 0 and pc > 0) or not _window_is_clean(closes):
            excluded += 1
            continue
        span = hi - lo if _finite(hi, lo) else float("nan")
        pos = (c - lo) / span if span == span and span > 0 else None            # a zero-range day has no "top of the range"
        sub = sub.sort_values("session_date")
        h20 = float(hi20.get(sym, np.nan))
        vm = float(vol_med.get(sym, np.nan))
        vol = float(w.volume[sym].iloc[-1])
        rows.append({
            "symbol": sym, "close": c, "prev_close": pc, "ret1_pct": (c / pc - 1) * 100,
            "listed_sessions": int(pd.to_datetime(sub["session_date"]).nunique()),
            "first_listed": pd.Timestamp(sub["session_date"].iloc[0]).date(),
            "last_listed": pd.Timestamp(sub["session_date"].iloc[-1]).date(),
            "category": str(sub["category"].iloc[-1]),
            "range_pos": pos, "at_day_high": (pos is not None and pos >= AT_HIGH_POS),
            "above_20d_high": bool(np.isfinite(h20) and c > h20),
            "rvol": (vol / vm) if np.isfinite(vm) and vm > 0 and np.isfinite(vol) else None,
            "closes": [float(x) for x in closes], "dates": [pd.Timestamp(d).date() for d in chart_dates],
            "ohlc": [_bar(o, h, l, cl) for o, h, l, cl in zip(w.open[sym].iloc[-CHART_SESSIONS:], w.high[sym].iloc[-CHART_SESSIONS:],
                                                              w.low[sym].iloc[-CHART_SESSIONS:], closes)],
        })
    if not rows:
        return None
    higher = sorted((r for r in rows if r["ret1_pct"] > 0), key=lambda r: (-r["ret1_pct"], r["symbol"]))
    if not higher:
        return None
    return {
        "session": ts.date(), "window": window, "n_sessions": len(window),
        "n_pool": len(rows) + excluded, "n_measured": len(rows), "n_excluded": excluded,
        "higher": len(higher), "lower": sum(1 for r in rows if r["ret1_pct"] < 0), "flat": sum(1 for r in rows if r["ret1_pct"] == 0),
        "at_day_high": sum(1 for r in rows if r["at_day_high"]), "above_20d_high": sum(1 for r in rows if r["above_20d_high"]),
        "top": higher[:PODIUM], "more": higher[PODIUM:top_n],
    }
