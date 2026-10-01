"""The screener's universe guards as a pure per-symbol decision with named reasons.

Extracted from `MultiTimeframeMLScreener._apply_universe_guards` so the screener (which drops failing symbols)
and the research observer (which records WHY a candidate was dropped) apply one definition, not two. The rules
are exactly the digest's, via the functions the digest and ML training already trust:

  * insufficient_history      fewer than digest_builder.MIN_BARS bars with a close
  * illiquid_dollar_volume    prior-20-session average dollar volume (dollar_vol_20 at T0-1, i.e. excluding the
                              signal day) below price_features.MIN_DOLLAR_VOLUME_20, or not computable
  * price_discontinuity       an unadjusted split / bad vendor bar within the last price_features.LOOKBACK_BARS

Pass == no reasons. Insufficient history short-circuits (the other two are meaningless on a short history);
otherwise every failing rule is reported.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from alerts import digest_builder as dbld
from ml_training.features import price_features as pf

INSUFFICIENT_HISTORY = "insufficient_history"
ILLIQUID_DOLLAR_VOLUME = "illiquid_dollar_volume"
PRICE_DISCONTINUITY = "price_discontinuity"


def prepare(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    px = pd.DataFrame(rows)
    for c in pf.OHLCV:
        px[c] = pd.to_numeric(px[c], errors="coerce")
    return px.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def reasons_for_frame(px: pd.DataFrame) -> List[str]:
    n = len(px)
    if n < dbld.MIN_BARS:
        return [INSUFFICIENT_HISTORY]
    reasons: List[str] = []
    ind = pf.compute_indicators(px)
    # Prior 20 sessions, excluding the signal day -- identical to send_daily_digest.analyse_universe's dv20
    # (there: g[...].iloc[-21:-1]).
    dv20 = ind["dollar_vol_20"].iloc[-2] if n >= 2 else np.nan
    if not (pd.notna(dv20) and dv20 >= pf.MIN_DOLLAR_VOLUME_20):
        reasons.append(ILLIQUID_DOLLAR_VOLUME)
    disc = pf.find_discontinuities(px)
    if len(disc) and (disc["pos"] >= (n - 1) - pf.LOOKBACK_BARS).any():
        reasons.append(PRICE_DISCONTINUITY)
    return reasons


def reasons_for_rows(rows: List[Dict[str, Any]]) -> List[str]:
    """`rows`: one symbol's stock_prices rows with date <= the session. No rows at all is insufficient history."""
    if not rows:
        return [INSUFFICIENT_HISTORY]
    return reasons_for_frame(prepare(rows))
