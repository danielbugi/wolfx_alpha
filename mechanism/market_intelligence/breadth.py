"""breadth_v1 -- market and per-sector breadth as explicit counts. Pure (pandas / numpy): no database, no network, no clock.

Every breadth metric is reported as a count record, never as a bare percentage:

    universe      stocks of the group that have a bar on the session
    denominator   ELIGIBLE: stocks for which the metric is computable (enough history, a bar on the session, valid prices)
    numerator     eligible stocks that satisfy the metric's condition
    missing       universe - denominator (stocks that could not be evaluated)
    pct           100 * numerator / denominator, or None when the denominator is below the minimum

A stock that cannot be evaluated is MISSING, never bearish: it leaves the denominator, it is not counted as "not above" or "not advancing".
`state`: ok | insufficient_eligible (pct withheld, counts kept) | no_universe | no_session.

Metrics (all descriptive, none is a signal or a Donchian input):
    above_sma50 / above_sma200   close > simple average of the last N closes (needs N bars)
    new_high_52w / new_low_52w   today's high >= highest high / today's low <= lowest low of the last 252 sessions (needs >= 200 bars)
    positive_ret_20              20-session close-to-close return > 0 (participation in an advance)
    advancing_1d / declining_1d  close above / below the previous session's close

The market-level above_sma50 / above_sma200 / new_high / new_low counts are the same numbers `risk_regime_v1` stores in c3/c4/c5; a test pins
that equality so the two can never silently diverge. (breadth_v1 additionally treats a non-positive close as missing; risk_regime_v1 does not.)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Optional

import numpy as np
import pandas as pd

BREADTH_VERSION = "breadth_v1"
MIN_ELIGIBLE_MARKET = 1_000
MIN_ELIGIBLE_SECTOR = 5
REAL_SESSION_SHARE = 0.5
YEAR_SESSIONS = 252
MIN_HISTORY_YEAR = 200

METRICS = ("above_sma50", "above_sma200", "new_high_52w", "new_low_52w", "positive_ret_20", "advancing_1d", "declining_1d")

OK, INSUFFICIENT, NO_UNIVERSE, NO_SESSION = "ok", "insufficient_eligible", "no_universe", "no_session"


def _count_record(universe: int, eligible: Optional[pd.Series], hit: Optional[pd.Series], minimum: int) -> Dict[str, Any]:
    """`eligible` / `hit` are boolean Series over the group's symbols (hit is only meaningful where eligible)."""
    if universe == 0:
        return {"numerator": 0, "denominator": 0, "universe": 0, "missing": 0, "pct": None, "state": NO_UNIVERSE}
    den = int(eligible.sum())
    num = int((hit & eligible).sum())
    pct = round(100.0 * num / den, 4) if den >= minimum else None
    return {"numerator": num, "denominator": den, "universe": universe, "missing": universe - den, "pct": pct,
            "state": OK if pct is not None else INSUFFICIENT}


def _empty(state: str) -> Dict[str, Any]:
    return {"numerator": None, "denominator": None, "universe": None, "missing": None, "pct": None, "state": state}


def _real_sessions(df: pd.DataFrame, counts: pd.Series) -> pd.DataFrame:
    return df.loc[counts >= REAL_SESSION_SHARE * counts.max()]


def _masks(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame) -> Dict[str, tuple]:
    """metric -> (eligible, hit) boolean Series over `close.columns`, for the LAST row of the (real-session) matrices."""
    c_t = close.iloc[-1]
    on_t = c_t.notna() & (c_t > 0)
    out: Dict[str, tuple] = {}
    for key, n in (("above_sma50", 50), ("above_sma200", 200)):
        sma = close.rolling(n, min_periods=n).mean().iloc[-1]
        elig = sma.notna() & on_t
        out[key] = (elig, (c_t > sma) & elig)
    hi = high.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).max().iloc[-1]
    lo = low.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).min().iloc[-1]
    hi_t, lo_t = high.iloc[-1], low.iloc[-1]
    ok = hi.notna() & hi_t.notna() & lo.notna() & lo_t.notna()
    out["new_high_52w"] = (ok, (hi_t >= hi) & ok)
    out["new_low_52w"] = (ok, (lo_t <= lo) & ok)
    if len(close) >= 21:
        c_0 = close.iloc[-21]
        elig = on_t & c_0.notna() & (c_0 > 0)
        out["positive_ret_20"] = (elig, (c_t > c_0) & elig)
    else:
        z = pd.Series(False, index=close.columns)
        out["positive_ret_20"] = (z, z)
    if len(close) >= 2:
        c_1 = close.iloc[-2]
        elig = on_t & c_1.notna() & (c_1 > 0)
        out["advancing_1d"] = (elig, (c_t > c_1) & elig)
        out["declining_1d"] = (elig, (c_t < c_1) & elig)
    else:
        z = pd.Series(False, index=close.columns)
        out["advancing_1d"] = out["declining_1d"] = (z, z)
    return out


def _group_records(masks: Mapping[str, tuple], members: pd.Index, minimum: int) -> Dict[str, Dict[str, Any]]:
    return {m: _count_record(len(members), masks[m][0][members], masks[m][1][members], minimum) for m in METRICS}


def compute(session_date: date, close: Optional[pd.DataFrame], high: Optional[pd.DataFrame], low: Optional[pd.DataFrame],
            sector_of: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Market and per-sector breadth for `session_date`. Matrices are date x symbol; bars after the session are ignored.

    Returns {"version", "session_date", "session_is_real", "market": {metric: record}, "sectors": {sector: {"universe", "metrics"}},
    "n_universe", "n_unclassified"}. A session that is not a real session of the panel yields state `no_session` everywhere, never zeros.
    """
    t = pd.Timestamp(session_date)
    base: Dict[str, Any] = {"version": BREADTH_VERSION, "session_date": session_date.isoformat(), "session_is_real": False,
                            "market": {m: _empty(NO_SESSION) for m in METRICS}, "sectors": {}, "n_universe": 0, "n_unclassified": 0,
                            "min_eligible_market": MIN_ELIGIBLE_MARKET, "min_eligible_sector": MIN_ELIGIBLE_SECTOR}
    if close is None or high is None or low is None or len(close) == 0:
        return base
    close, high, low = close.loc[:t], high.loc[:t], low.loc[:t]
    if len(close) == 0:
        return base
    counts = close.notna().sum(axis=1)
    close, high, low = _real_sessions(close, counts), _real_sessions(high, counts), _real_sessions(low, counts)
    if len(close) == 0 or close.index[-1] != t:
        return base
    high, low = high.reindex(index=close.index, columns=close.columns), low.reindex(index=close.index, columns=close.columns)
    masks = _masks(close, high, low)
    on_session = close.columns[close.iloc[-1].notna()]
    base["session_is_real"] = True
    base["n_universe"] = int(len(on_session))
    base["market"] = _group_records(masks, on_session, MIN_ELIGIBLE_MARKET)
    sectors = pd.Series({s: _clean_sector(sector_of.get(s)) if sector_of else None for s in on_session}, dtype=object)
    base["n_unclassified"] = int(sectors.isna().sum())
    for sec in sorted(set(sectors.dropna())):
        members = sectors.index[sectors == sec]
        base["sectors"][sec] = {"universe": int(len(members)), "metrics": _group_records(masks, members, MIN_ELIGIBLE_SECTOR)}
    return base


def _clean_sector(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    return None if (not s or s.lower() == "unknown") else s
