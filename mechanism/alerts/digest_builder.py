# mechanism/alerts/digest_builder.py
"""
"First Light" digest (long side): put every liquid symbol into a Donchian group, then rank three lists per group.
Pure logic (no database / network) so it is unit-testable; see send_daily_digest.py for the I/O.

Groups (identical to multi_timeframe_screener.py's signal_type rules, evaluated on the session bar):
  breakout       close crossed the PRIOR bar's 20-day high while the previous close was still inside it
  near_breakout  within NEAR_PCT below the 20-day high (and not a breakout)
The short side (breakdown / near_breakdown) is still classified, so "was in this group yesterday" is exact, but the
digest only reports LONG_CATEGORIES.

Lists (top N each, only stocks with prior-20-day average dollar volume >= min_dv):
  gainers  biggest 1-day % gain
  atr      today's true range / prior-day ATR14
  volume   today's volume / median of the prior 50 sessions
Everything is a descriptive fact about public prices, not a validated signal (see CLAUDE.md, 2026-09-20).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from ml_training.features import price_features as pf  # noqa: E402
from alerts.alert_builder import Skip  # noqa: E402
from alerts.star import STAR_RANK, is_starred, starred_lists  # noqa: E402,F401

CATEGORIES = ("breakout", "near_breakout", "breakdown", "near_breakdown")
LONG_CATEGORIES = ("breakout", "near_breakout")
LISTS = ("gainers", "atr", "volume")
NEAR_PCT = 3.0                  # same 3% as the screener's near-breakout
MIN_BARS = 60                   # 50-session volume median + margin; shorter histories are skipped, not defaulted
DEFAULT_MIN_DV = 5_000_000.0    # ranked lists only; the universe floor (pf.MIN_DOLLAR_VOLUME_20) still applies to counts


def _classify(c: np.ndarray, dh: np.ndarray, dl: np.ndarray, i: int) -> Optional[str]:
    ci, pc, pdh, pdl = c[i], c[i - 1], dh[i - 1], dl[i - 1]
    if not (np.isfinite([ci, pc, pdh, pdl, dh[i], dl[i]]).all() and ci > 0 and dh[i] > dl[i] and pdh > pdl):
        return None
    if ci > pdh and pc <= pdh:
        return "breakout"
    if ci < pdl and pc >= pdl:
        return "breakdown"
    if 0 < (dh[i] - ci) / ci * 100 <= NEAR_PCT:
        return "near_breakout"
    if 0 < (ci - dl[i]) / ci * 100 <= NEAR_PCT:
        return "near_breakdown"
    return None


def analyze(px: pd.DataFrame) -> Dict:
    """Facts for the LAST bar of one symbol's history (oldest first), for every stock -- 'cat' is None when it is in
    no group (the snapshot keeps those too, so a watchlist can report on any name).
    Raises Skip when the data cannot be trusted (too short, a price discontinuity in the lookback)."""
    px = px.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    for col in pf.OHLCV:
        px[col] = pd.to_numeric(px[col], errors="coerce")
    n = len(px)
    if n < MIN_BARS:
        raise Skip(f"fewer than {MIN_BARS} bars of history")
    i = n - 1
    disc = pf.find_discontinuities(px)
    if len(disc) and (disc["pos"] >= i - pf.LOOKBACK_BARS).any():
        raise Skip("price discontinuity in the last 253 bars (unadjusted split / bad data)")
    ind = pf.compute_indicators(px)
    c, dh, dl = ind["close"].to_numpy(), ind["dh"].to_numpy(), ind["dl"].to_numpy()
    cat = _classify(c, dh, dl, i)
    r = ind.iloc[i]
    ci, pc = float(c[i]), float(c[i - 1])
    prev_atr = float(ind["atr"].iloc[i - 1])
    vol_med50 = float(np.median(ind["volume"].to_numpy()[i - 50:i]))
    tr = max(float(r["high"]) - float(r["low"]), abs(float(r["high"]) - pc), abs(float(r["low"]) - pc))
    prev_cat = _classify(c, dh, dl, i - 1)
    return {
        "cat": cat, "prev_cat": prev_cat, "new": cat != prev_cat,
        "close": ci, "ret1_pct": (ci / pc - 1) * 100,
        "atr": float(r["atr"]) if np.isfinite(r["atr"]) else None,       # ATR(14) of the session bar, price units
        "dv20": float(ind["dollar_vol_20"].iloc[i - 1]),
        "rvol": float(r["volume"]) / vol_med50 if vol_med50 > 0 else None,
        "range_atr": tr / prev_atr if prev_atr and prev_atr > 0 else None,
        "below_high_pct": (float(dh[i]) - ci) / ci * 100 if np.isfinite(dh[i]) else None,
    }


def build_digest(rows: List[Dict], top_n: int = 5, min_dv: float = DEFAULT_MIN_DV,
                 categories: Iterable[str] = LONG_CATEGORIES) -> Dict:
    """rows = analyze() results (each with a 'symbol'). Returns counts, the ranked lists and, per group, the symbols
    that are in the top STAR_RANK of 2+ lists ("multi", with those list names). Each listed row also gets 'list_ranks' ({list: rank})."""
    counts, boards = {}, {}
    for cat in categories:
        members = [r for r in rows if r["cat"] == cat]
        counts[cat] = len(members)
        pool = [r for r in members if r["dv20"] >= min_dv]
        lists = {
            "gainers": sorted(pool, key=lambda r: (-r["ret1_pct"], r["symbol"]))[:top_n],
            "atr": sorted([r for r in pool if r["range_atr"] is not None], key=lambda r: (-r["range_atr"], r["symbol"]))[:top_n],
            "volume": sorted([r for r in pool if r["rvol"] is not None], key=lambda r: (-r["rvol"], r["symbol"]))[:top_n],
        }
        membership: Dict[str, List[str]] = {}
        ranks_of: Dict[str, Dict[str, int]] = {}
        for name in LISTS:
            for rank, r in enumerate(lists[name], 1):
                membership.setdefault(r["symbol"], []).append(name)
                r.setdefault("list_ranks", {})[name] = rank
                ranks_of[r["symbol"]] = r["list_ranks"]
        boards[cat] = {**lists, "eligible": len(pool), "membership": membership,
                       "multi": {s: starred_lists(rk) for s, rk in ranks_of.items() if is_starred(rk)}}
    return {"counts": counts, "boards": boards, "min_dv": min_dv}
