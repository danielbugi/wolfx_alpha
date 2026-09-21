# mechanism/alerts/scoreboard.py
"""
List scoreboard (CHANNEL_CONTENT_MILESTONES.md M5.1, report item D12): what happened to the stocks in each day's channel lists 1, 5 and 20
sessions later, next to ALL liquid stocks over the same sessions. Pure pandas (no database, no network): the loader lives in
send_channel_posts.py, the wording in channel_content.post_scoreboard.

Honesty rules baked in:
  * a stock appears once per session however many lists it is in ("stock-day"); consecutive days of the same stock overlap, so the numbers
    are descriptive counts, not independent trials (the post says so);
  * a horizon is only reported when at least MIN_SESSIONS sessions have that many later sessions, and never extrapolated;
  * the comparison is the median / share-higher of every liquid stock over the same sessions (not a hand-picked index);
  * a window that contains a split-shaped jump (price_guard.is_split_like: beyond ~3x or a common split factor such as 2-for-1) is left out for that stock, never repaired;
  * snapshots reconstructed by backfill_snapshots.py use today's adjusted prices; the post states the date range it covers.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from alerts.price_guard import is_split_like

HORIZONS = (1, 5, 20)
MIN_SESSIONS = 5                                # fewer sessions than this behind a horizon is too little to state anything
GROUPS = ("breakout", "near_breakout")


def forward_changes(closes: pd.DataFrame, session, k: int) -> Optional[pd.Series]:
    """% change from the close on `session` to the close k sessions later, per symbol. None when the session is unknown or fewer than k later
    sessions exist. Symbols with a missing bar in the window, or a day-to-day jump beyond the discontinuity rule, get NaN (left out)."""
    ts = pd.Timestamp(session)
    i = closes.index.get_indexer([ts])[0]
    if i < 0 or i + k >= len(closes):
        return None
    window = closes.iloc[i:i + k + 1]
    ratios = (window / window.shift(1)).iloc[1:]
    jumped = ratios.apply(lambda col: col.map(lambda r: r == r and is_split_like(float(r))).any())      # the shared split-shape rule (2-for-1 etc.)
    bad = window.isna().any() | jumped
    chg = (window.iloc[-1] / window.iloc[0] - 1) * 100
    return chg.where(~bad)


def _stats(values: Sequence[float]) -> Optional[Dict]:
    v = np.asarray([x for x in values if x == x], dtype=float)
    if v.size == 0:
        return None
    return {"n": int(v.size), "up_share": float((v > 0).mean() * 100), "median": float(np.median(v))}


def compute(appearances: pd.DataFrame, universe: Dict, closes: pd.DataFrame, horizons: Iterable[int] = HORIZONS) -> Dict:
    """appearances: columns session_date, symbol, category (one row per stock-day in the lists). universe: {session_date: [symbols]} of every liquid stock
    that day. closes: dates x symbols. Returns {"first": date, "last": date, "sessions": n, "groups": {group: {horizon: {...}}}} with only the
    horizons that have at least MIN_SESSIONS sessions behind them."""
    out: Dict = {"groups": {g: {} for g in GROUPS}, "first": None, "last": None, "sessions": int(appearances["session_date"].nunique()) if len(appearances) else 0}
    if not len(appearances):
        return out
    out["first"], out["last"] = min(appearances["session_date"]), max(appearances["session_date"])
    for k in horizons:
        per_session: Dict = {}
        for sd in sorted(appearances["session_date"].unique()):
            fc = forward_changes(closes, sd, k)
            if fc is not None:
                per_session[sd] = fc
        if len(per_session) < MIN_SESSIONS:
            continue
        uni_vals: List[float] = []
        for sd, fc in per_session.items():
            uni_vals += [float(fc[s]) for s in universe.get(sd, []) if s in fc.index and fc[s] == fc[s]]
        uni = _stats(uni_vals)
        for g in GROUPS:
            vals: List[float] = []
            rows = appearances[appearances["category"] == g]
            for sd, sub in rows.groupby("session_date"):
                fc = per_session.get(sd)
                if fc is not None:
                    vals += [float(fc[s]) for s in sub["symbol"] if s in fc.index and fc[s] == fc[s]]
            st = _stats(vals)
            if st and uni:
                out["groups"][g][k] = {**st, "sessions": len(per_session), "universe": uni}
    return out
