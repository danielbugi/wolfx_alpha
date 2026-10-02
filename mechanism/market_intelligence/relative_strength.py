"""Canonical sector and relative-strength definitions, `rs_v1`. Pure (pandas / numpy): no database, no network, no clock.

Per session `t` and horizon `h` in {5, 20, 60} sessions ("session" = a real trading session, i.e. a date of the filtered session index):

  stock return        ret_h = (C_t / C_{t-h} - 1) * 100            NULL if C_t or C_{t-h} is missing / non-positive, fewer than h+1 sessions are held,
                                                                    or the symbol has a detected price discontinuity dated in (t-h, t]
  sector return       median of the valid member returns          NULL if fewer than MIN_SECTOR_MEMBERS (5) valid members: counts stay, return/rank are NULL
  universe median     median of ALL valid stock returns           NULL if fewer than MIN_UNIVERSE (1,000) valid stocks
  four separate comparisons, never mixed:
      stock  vs broad market   ret_h - spx_ret_h                        (percentage points; ^GSPC close)
      stock  vs sector         ret_h - sec_ret_h(sector)                (pp)
      sector vs ^GSPC          sec_ret_h - spx_ret_h                    (pp)   -- cap-weighted index vs an equal-weight median: NOT like-for-like
      sector vs universe median sec_ret_h - univ_ret_h                  (pp)   -- the like-for-like (equal-weight) benchmark
  RS percentile       100 * (#{ret < x} + 0.5 * #{ret = x}) / N, N stored; NULL if ret_h is NULL or N < MIN_UNIVERSE
  sector rank         by sec_ret_20 descending, ties by sector name ascending; sectors without a value are not ranked

No liquidity / price / market-cap filter in v1 (a deliberate, versioned change if ever wanted). Equal weight, one vote per stock.
A stock with no sector is excluded from sector medians but counted in the universe median; it is never given an 'Unknown' pseudo-sector.

SECTOR MAP POINT-IN-TIME STATUS: the map comes from `daily_fundamentals.sector` (a yfinance `info` field, rewritten in place, bulk-loaded with lag).
A map for a past session is therefore NOT provably what was known then; see `sector_map_semantics`. Only a forward capture is `observed`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

MODEL_VERSION = "rs_v1"
HORIZONS = (5, 20, 60)
RANK_HORIZON = 20
MIN_SECTOR_MEMBERS = 5
MIN_UNIVERSE = 1_000
REAL_SESSION_SHARE = 0.5
SPX = "^GSPC"
SECTOR_SOURCE = "daily_fundamentals.sector"


def clean_sector(v: Any) -> Optional[str]:
    """NULL, blank and the legacy 'Unknown' placeholder all mean 'no sector' -- never a sector of their own."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    return None if (not s or s.lower() == "unknown") else s


def sector_map_semantics(provenance: str, asof_rule: str = "latest daily_fundamentals row with date <= session") -> Dict[str, Any]:
    """What the stored sector map is allowed to claim. Only an `observed` map (frozen by the pipeline run for that session) can be
    called what-was-known-then; a `reconstructed` one is today's metadata projected backwards."""
    if provenance not in ("observed", "reconstructed"):
        raise ValueError(f"provenance must be observed or reconstructed, not {provenance!r}")
    return {
        "source": SECTOR_SOURCE, "asof_rule": asof_rule, "provenance": provenance,
        "pit_safe": provenance == "observed",
        "caveat": None if provenance == "observed" else
        "sector tags are the latest held at reconstruction time (rows are updated in place); not proven to be what was known on the session",
    }


@dataclass
class SectorRow:
    sector: str
    n_members: int                                   # classified symbols with a bar on t
    per_horizon: Dict[int, Dict[str, Optional[float]]]   # h -> n_valid, n_excluded, ret, vs_spx, vs_univ
    rank_20: Optional[int] = None

    def to_record(self) -> Dict[str, Any]:
        return {"sector": self.sector, "n_members": self.n_members, "rank_20": self.rank_20,
                "horizons": {str(h): v for h, v in self.per_horizon.items()}}


@dataclass
class RelativeStrength:
    session_date: date
    model_version: str
    horizons: Tuple[int, ...]
    spx_ret: Dict[int, Optional[float]]
    universe: Dict[int, Dict[str, Optional[float]]]  # h -> ret (median), n_valid
    sectors: List[SectorRow]
    stocks: pd.DataFrame                              # index symbol; sector, ret_h, vs_spx_h, vs_sector_h, rs_pctile_h
    coverage: Dict[str, Any] = field(default_factory=dict)

    def sector_records(self) -> List[Dict[str, Any]]:
        return [s.to_record() for s in self.sectors]

    def session_record(self) -> Dict[str, Any]:
        return {"model_version": self.model_version, "session_date": self.session_date.isoformat(),
                "spx_ret": {str(h): v for h, v in self.spx_ret.items()},
                "universe": {str(h): v for h, v in self.universe.items()}, "coverage": self.coverage}


def real_sessions_only(close: pd.DataFrame) -> pd.DataFrame:
    counts = close.notna().sum(axis=1)
    return close.loc[counts >= REAL_SESSION_SHARE * counts.max()]


def index_return(s: Optional[pd.Series], t: pd.Timestamp, h: int) -> Optional[float]:
    if s is None:
        return None
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    s = s.sort_index().loc[:t]
    if len(s) < h + 1 or s.index[-1] != t:
        return None
    c0 = float(s.iloc[-1 - h])
    return float((float(s.iloc[-1]) / c0 - 1) * 100) if c0 > 0 else None


def stock_returns(close: pd.DataFrame, h: int, disc_dates: Mapping[str, Iterable[Any]]) -> Tuple[pd.Series, int]:
    """(ret_h per symbol for the LAST row of `close`, number nulled by a discontinuity). `close`'s last row must be the session.
    NaN where invalid, per the module docstring."""
    if len(close) < h + 1:
        return pd.Series(np.nan, index=close.columns, dtype=float), 0
    c_t, c_h = close.iloc[-1], close.iloc[-1 - h]
    ok = c_t.notna() & c_h.notna() & (c_t > 0) & (c_h > 0)
    ret = ((c_t / c_h - 1) * 100).where(ok).astype(float)
    lo, hi = close.index[-1 - h], close.index[-1]
    nulled = 0
    for sym, dates in disc_dates.items():
        if sym in ret.index and pd.notna(ret[sym]) and any(lo < pd.Timestamp(d) <= hi for d in dates):
            ret[sym] = np.nan
            nulled += 1
    return ret, nulled


def _median(vals: pd.Series, minimum: int) -> Tuple[Optional[float], int]:
    v = vals.dropna()
    return (float(np.median(v.to_numpy())) if len(v) >= minimum else None), int(len(v))


def _pct_rank(ret: pd.Series) -> pd.Series:
    n = int(ret.notna().sum())
    if n < MIN_UNIVERSE:
        return pd.Series(np.nan, index=ret.index, dtype=float)
    r = ret.rank(method="average")                      # average rank = #less + (#equal + 1) / 2
    return (100.0 * (r - 0.5) / n).where(ret.notna())


def _sub(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else float(a - b)


def compute(session_date: date, close: pd.DataFrame, sector_of: Mapping[str, Any], index_closes: Mapping[str, pd.Series],
            discontinuities: Mapping[str, Iterable[Any]], sector_provenance: str = "reconstructed",
            horizons: Tuple[int, ...] = HORIZONS) -> RelativeStrength:
    """`close`: date x symbol closes (as `alerts.market_stats.to_wide(...).close`). `discontinuities`: {symbol: [dates]} from
    `price_discontinuities` -- REQUIRED (pass {} to assert none were detected; an absent argument is not silently 'none').
    `sector_of`: {symbol: sector or None}. `session_date` is explicit; later bars are ignored."""
    if RANK_HORIZON not in horizons:
        raise ValueError(f"horizons must include {RANK_HORIZON} (the ranking horizon)")
    t = pd.Timestamp(session_date)
    sem = sector_map_semantics(sector_provenance)
    empty_cov = {"session_is_real": False, "n_universe": 0, "n_classified": 0, "n_unclassified": 0, "sector_map": sem}
    close = real_sessions_only(close.loc[:t]) if len(close) else close
    spx = {h: index_return(index_closes.get(SPX), t, h) for h in horizons}
    if len(close) == 0 or close.index[-1] != t:
        return RelativeStrength(session_date, MODEL_VERSION, tuple(horizons), spx,
                                {h: {"ret": None, "n_valid": 0} for h in horizons}, [], pd.DataFrame(), empty_cov)

    syms = list(close.columns[close.iloc[-1].notna()])
    close = close[syms]
    sectors = pd.Series({s: clean_sector(sector_of.get(s)) for s in syms}, dtype=object)
    stocks = pd.DataFrame({"sector": sectors})
    in_universe = set(syms)
    disc = {s: d for s, d in discontinuities.items() if s in in_universe}
    rets, cov_disc = {}, {}
    for h in horizons:
        rets[h], cov_disc[str(h)] = stock_returns(close, h, disc)

    univ: Dict[int, Dict[str, Optional[float]]] = {}
    sector_rows: Dict[str, SectorRow] = {}
    for sec in sorted(set(sectors.dropna())):
        members = list(sectors.index[sectors == sec])
        sector_rows[sec] = SectorRow(sec, len(members), {})
    for h in horizons:
        r = rets[h]
        u_ret, n_valid = _median(r, MIN_UNIVERSE)
        univ[h] = {"ret": u_ret, "n_valid": n_valid}
        stocks[f"ret_{h}"] = r
        stocks[f"vs_spx_{h}"] = r - spx[h] if spx[h] is not None else np.nan
        stocks[f"rs_pctile_{h}"] = _pct_rank(r)
        sec_ret_map: Dict[str, Optional[float]] = {}
        for sec, row in sector_rows.items():
            members = sectors.index[sectors == sec]
            s_ret, s_valid = _median(r[members], MIN_SECTOR_MEMBERS)
            sec_ret_map[sec] = s_ret
            row.per_horizon[h] = {"n_valid": s_valid, "n_excluded": row.n_members - s_valid, "ret": s_ret,
                                  "vs_spx": _sub(s_ret, spx[h]), "vs_univ": _sub(s_ret, u_ret)}
        stocks[f"vs_sector_{h}"] = sectors.map(lambda s: sec_ret_map.get(s) if s is not None else None).astype(float)
        stocks[f"vs_sector_{h}"] = stocks[f"ret_{h}"] - stocks[f"vs_sector_{h}"]

    ranked = sorted((row for row in sector_rows.values() if row.per_horizon[RANK_HORIZON]["ret"] is not None),
                    key=lambda row: (-row.per_horizon[RANK_HORIZON]["ret"], row.sector))
    for i, row in enumerate(ranked, start=1):
        row.rank_20 = i

    n_class = int(sectors.notna().sum())
    coverage = {"session_is_real": True, "n_universe": len(syms), "n_classified": n_class, "n_unclassified": len(syms) - n_class,
                "classified_share": round(n_class / len(syms), 4) if syms else None,
                "n_sectors": len(sector_rows), "n_sectors_ranked": len(ranked),
                "n_excluded_discontinuity": cov_disc, "min_sector_members": MIN_SECTOR_MEMBERS, "min_universe": MIN_UNIVERSE,
                "sector_map": sem}
    return RelativeStrength(session_date, MODEL_VERSION, tuple(horizons), spx, univ,
                            sorted(sector_rows.values(), key=lambda r: r.sector), stocks, coverage)
