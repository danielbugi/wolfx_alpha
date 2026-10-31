"""Market context for an inflection event: the price reaction around the publication, the return relative to the stock's sector, and the valuation then and now.
Standard library + numpy; no database, clock or network (the caller hands in the data).

Point in time: every number is computed from sessions and snapshots dated on or before the date it is attributed to. The sector benchmark is the equal-weight return of the sector's
stocks in the supplied panel (the stock itself included: with the sector sizes involved the effect is small, and it is stated, not hidden). A sector with fewer than MIN_SECTOR_MEMBERS
members has no benchmark and the excess return is None.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

MIN_SECTOR_MEMBERS = 8
MIN_HISTORY_SNAPSHOTS = 8


@dataclass(frozen=True)
class Prices:
    dates: List[date]
    close: np.ndarray


def first_session_on_or_after(dates: Sequence[date], d: date) -> Optional[int]:
    i = bisect.bisect_left(dates, d)
    return i if i < len(dates) else None


def reaction(p: Prices, available_at: date, max_gap_days: int = 5) -> Dict[str, Any]:
    """Two-session reaction around the publication: close of the session before the first session on or after `available_at` to the close of the session after it."""
    D = first_session_on_or_after(p.dates, available_at)
    if D is None or (p.dates[D] - available_at).days > max_gap_days or D < 1 or D + 1 >= len(p.dates):
        return {"ret_2d": None, "report_session": None if D is None else p.dates[D].isoformat()}
    return {"ret_2d": float(p.close[D + 1] / p.close[D - 1] - 1.0), "report_session": p.dates[D].isoformat()}


class SectorIndex:
    """Equal-weight sector levels on a common calendar. Built once per run from the price panel."""

    def __init__(self, prices: Mapping[str, Prices], sector_of: Mapping[str, str]) -> None:
        cal = sorted({d for p in prices.values() for d in p.dates})
        self.dates = cal
        pos = {d: i for i, d in enumerate(cal)}
        by_sector: Dict[str, List[str]] = {}
        for s in prices:
            sec = sector_of.get(s)
            if sec:
                by_sector.setdefault(sec, []).append(s)
        self.levels: Dict[str, np.ndarray] = {}
        self.members: Dict[str, int] = {}
        for sec, syms in by_sector.items():
            if len(syms) < MIN_SECTOR_MEMBERS:
                continue
            mat = np.full((len(syms), len(cal)), np.nan)
            for r, s in enumerate(syms):
                p = prices[s]
                idx = np.fromiter((pos[d] for d in p.dates), dtype=int, count=len(p.dates))
                mat[r, idx] = p.close
            with np.errstate(invalid="ignore", divide="ignore"):
                ret = mat[:, 1:] / mat[:, :-1] - 1.0
            ret = np.where(np.isfinite(ret) & (np.abs(ret) < 1.0), ret, np.nan)       # a +-100% day is a data artifact, not a sector move
            with np.errstate(invalid="ignore"):
                mean = np.nanmean(ret, axis=0)
            mean = np.where(np.isfinite(mean), mean, 0.0)
            self.levels[sec] = np.r_[1.0, np.cumprod(1.0 + mean)]
            self.members[sec] = len(syms)

    def ret(self, sector: Optional[str], d0: date, d1: date) -> Optional[float]:
        if sector not in self.levels:
            return None
        i0, i1 = first_session_on_or_after(self.dates, d0), first_session_on_or_after(self.dates, d1)
        if i0 is None or i1 is None:
            return None
        lv = self.levels[sector]
        return float(lv[i1] / lv[i0] - 1.0)


def excess_return(p: Prices, idx: Optional[SectorIndex], sector: Optional[str], d0: date, d1: date) -> Optional[float]:
    """Stock return from the close on `d0` to the close on `d1` minus the sector's, on the supplied sessions; None when any piece is missing."""
    if idx is None:
        return None
    i0, i1 = first_session_on_or_after(p.dates, d0), first_session_on_or_after(p.dates, d1)
    if i0 is None or i1 is None or i1 <= i0 or p.dates[i0] != d0 or p.dates[i1] != d1:
        return None
    s = idx.ret(sector, d0, d1)
    if s is None:
        return None
    return float(p.close[i1] / p.close[i0] - 1.0) - s


def repricing_window(p: Prices, available_at: date, sessions: int) -> Optional[Tuple[date, date]]:
    """(start, end) session dates of the repricing window: from the close of the session AFTER the reaction window to `sessions` sessions later; None when it has not completed."""
    D = first_session_on_or_after(p.dates, available_at)
    if D is None:
        return None
    b, e = D + 2, D + 2 + sessions
    if e >= len(p.dates):
        return None
    return p.dates[b], p.dates[e]


class ValuationStore:
    """Stored daily valuation snapshots: price/sales, price/earnings, market cap and sector per symbol and stored day."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.by_symbol: Dict[str, Tuple[List[date], List[Optional[float]], List[Optional[float]], List[Optional[float]]]] = {}
        self._sector_ps: Dict[Tuple[str, date], List[float]] = {}
        tmp: Dict[str, List[Tuple[date, Optional[float], Optional[float], Optional[float], Optional[str]]]] = {}
        for r in rows:
            d = r["date"] if isinstance(r["date"], date) else date.fromisoformat(str(r["date"])[:10])
            ps, pe, mc = _f(r.get("ps_ratio")), _f(r.get("pe_ratio")), _f(r.get("market_cap"))
            tmp.setdefault(r["symbol"], []).append((d, ps, pe, mc, r.get("sector") or None))
            if ps is not None and ps > 0 and r.get("sector"):
                self._sector_ps.setdefault((r["sector"], d), []).append(ps)
        self.sector_latest: Dict[str, str] = {}
        for sym, lst in tmp.items():
            lst.sort(key=lambda t: t[0])
            self.by_symbol[sym] = ([t[0] for t in lst], [t[1] for t in lst], [t[2] for t in lst], [t[3] for t in lst])
            secs = [t[4] for t in lst if t[4]]
            if secs:
                self.sector_latest[sym] = secs[-1]

    def context(self, symbol: str, at: date, sector: Optional[str]) -> Dict[str, Any]:
        """The snapshot on or before `at` (never after) with the stock's own percentile and its ratio to the sector median that day. Missing pieces are None."""
        out: Dict[str, Any] = {"snapshot_date": None, "ps_ratio": None, "pe_ratio": None, "market_cap": None, "ps_percentile_own_history": None, "ps_vs_sector_median": None}
        row = self.by_symbol.get(symbol)
        if not row:
            return out
        dates, ps, pe, mc = row
        i = bisect.bisect_right(dates, at) - 1
        if i < 0:
            return out
        out.update(snapshot_date=dates[i].isoformat(), ps_ratio=ps[i], pe_ratio=pe[i], market_cap=mc[i])
        hist = [v for v in ps[: i + 1] if v is not None and v > 0]
        if ps[i] is not None and ps[i] > 0 and len(hist) >= MIN_HISTORY_SNAPSHOTS:
            out["ps_percentile_own_history"] = float(sum(1 for v in hist if v <= ps[i]) / len(hist))
        peers = self._sector_ps.get((sector or "", dates[i]))
        if ps[i] is not None and ps[i] > 0 and peers and len(peers) >= MIN_SECTOR_MEMBERS:
            out["ps_vs_sector_median"] = float(ps[i] / float(np.median(peers)))
        return out


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None
