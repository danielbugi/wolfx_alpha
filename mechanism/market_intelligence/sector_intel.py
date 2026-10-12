"""sector_v1 -- per-sector measurements assembled from `rs_v1` (returns, comparisons, rank) and `breadth_v1` (counts). Pure: no database,
no network, no clock, no new arithmetic beyond a rank tercile; every number here is a stored measurement of one of the two inputs.

For each sector, four groups of measurements (a value that could not be computed is None, never 0):
    trend          member-median return over 5/20/60 sessions; share of members above their 50- / 200-day average
    strength       rank_20 (1 = strongest; sectors with < 5 valid members are unranked), return minus the S&P 500 (cap-weighted index vs an
                   equal-weight median: NOT like-for-like) and minus the universe median (like-for-like), and `strength_state`
    participation  share of members with a positive 20-session return, at a 52-week high / low, advancing / declining today
    coverage       members, valid members and the excluded count per horizon, so a thin sector is visible as thin

`strength_state` is a declared v1 label, not an estimate: leading = rank in the top third of the RANKED sectors, lagging = bottom third,
in_line = the rest, not_available = unranked or fewer than 3 ranked sectors. It is context only, never a Donchian filter or score input.
"""
from __future__ import annotations

from math import ceil
from typing import Any, Dict, List, Mapping, Optional

from market_intelligence.relative_strength import HORIZONS, RelativeStrength

SECTOR_VERSION = "sector_v1"
MIN_RANKED_FOR_STATE = 3


def strength_state(rank: Optional[int], n_ranked: int) -> str:
    if rank is None or n_ranked < MIN_RANKED_FOR_STATE:
        return "not_available"
    third = ceil(n_ranked / 3)
    if rank <= third:
        return "leading"
    if rank > n_ranked - third:
        return "lagging"
    return "in_line"


def build(rs: RelativeStrength, breadth: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One record per sector of `rs` (sorted by sector name), joined to that sector's breadth counts."""
    n_ranked = sum(1 for s in rs.sectors if s.rank_20 is not None)
    out: List[Dict[str, Any]] = []
    for s in rs.sectors:
        b = (breadth.get("sectors") or {}).get(s.sector, {})
        m = b.get("metrics", {})
        ph = s.per_horizon

        def pct(metric: str) -> Optional[float]:
            return (m.get(metric) or {}).get("pct")

        out.append({
            "sector": s.sector,
            "version": SECTOR_VERSION,
            "n_members": s.n_members,
            "trend": {**{f"ret_{h}": ph[h]["ret"] for h in HORIZONS if h in ph},
                      "pct_above_sma50": pct("above_sma50"), "pct_above_sma200": pct("above_sma200")},
            "strength": {"rank_20": s.rank_20, "n_ranked": n_ranked, "strength_state": strength_state(s.rank_20, n_ranked),
                         **{f"vs_spx_{h}": ph[h]["vs_spx"] for h in HORIZONS if h in ph},
                         **{f"vs_univ_{h}": ph[h]["vs_univ"] for h in HORIZONS if h in ph}},
            "participation": {k: m.get(k) for k in ("positive_ret_20", "new_high_52w", "new_low_52w", "advancing_1d", "declining_1d")},
            "breadth": {k: m.get(k) for k in ("above_sma50", "above_sma200")},
            "coverage": {str(h): {"n_valid": ph[h]["n_valid"], "n_excluded": ph[h]["n_excluded"]} for h in HORIZONS if h in ph},
        })
    return out
