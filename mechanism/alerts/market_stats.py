# mechanism/alerts/market_stats.py
"""
Market-wide facts for the channel's extra posts (CHANNEL_CONTENT_REPORT_2026-09-21.md, P1-P8). Pure pandas / numpy: no database, no network,
so it is unit-tested on synthetic prices. The input is the SAME long price table the daily digest loads (`send_daily_digest.load_universe_history`)
and the symbols are the ones the digest analysed, so every count agrees with the digest's own.

Definitions (all descriptive, none is a signal):
  Per-session breakout / near-breakout COUNTS are not recomputed here: they are read from the stored digest snapshots (digest_runs), so they always equal the digest's own.
  20-week high    highest high of the last 100 sessions;  52-week high = last 252 sessions (both include today's bar)
  above N-day     close above its simple N-day average (needs N bars; a stock with fewer is left out of that percentage)
A number that cannot be computed is None (never filled in).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

NEAR_PCT = 3.0
WEEKS20_SESSIONS = 100
YEAR_SESSIONS = 252
MIN_HISTORY_YEAR = 200            # a 52-week range from fewer than 200 bars is not a 52-week range
MIN_DV_LISTS = 5_000_000.0        # ranked lists only, like the digest
MIN_STOCKS_PER_SECTOR = 5
REAL_SESSION_SHARE = 0.5          # a date needs prices for at least half of the busiest date's symbols to count as a session


@dataclass
class Wide:
    """Date x symbol matrices, oldest date first."""
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame

    @property
    def dates(self) -> pd.Index:
        return self.close.index


def to_wide(df: pd.DataFrame, symbols: Optional[Iterable[str]] = None) -> Wide:
    d = df if symbols is None else df[df["symbol"].isin(set(symbols))]
    d = d.drop_duplicates(["symbol", "date"])

    def piv(col: str) -> pd.DataFrame:
        return d.pivot(index="date", columns="symbol", values=col).sort_index()
    wide = Wide(piv("open"), piv("high"), piv("low"), piv("close"), piv("volume"))
    # A date carried by only a handful of symbols (a partial load, a stray row on a holiday) is not a session: left in, it would put a NaN
    # into every stock's rolling window and silently empty the 200-day statistics.
    counts = wide.close.notna().sum(axis=1)
    real = counts >= REAL_SESSION_SHARE * counts.max()
    return Wide(*(m.loc[real] for m in (wide.open, wide.high, wide.low, wide.close, wide.volume)))


# ------------------------------------------------------------------ health (P1)
def pct_above_sma(w: Wide, n: int, tail: int = 60) -> Tuple[pd.Series, int]:
    """(% of stocks with a close above their n-day average, per session for the last `tail` sessions; how many stocks counted today)."""
    sma = w.close.rolling(n, min_periods=n).mean()
    valid = sma.notna() & w.close.notna()
    above = (w.close > sma) & valid
    count = valid.sum(axis=1)
    pct = (above.sum(axis=1) / count.where(count > 0)) * 100
    return pct.tail(tail), int(count.iloc[-1])


@dataclass
class Health:
    session: pd.Timestamp
    above50: pd.Series                 # % per session, last 60
    above200: pd.Series
    n50: int
    n200: int
    new_highs: int
    new_lows: int
    n_range: int                       # stocks with a usable 52-week range today
    ref_sessions: int                  # how many sessions back the "before" numbers are


def health(w: Wide, ref_sessions: int = 10) -> Health:
    a50, n50 = pct_above_sma(w, 50)
    a200, n200 = pct_above_sma(w, 200)
    hi = w.high.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).max().iloc[-1]
    lo = w.low.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).min().iloc[-1]
    hi_today, lo_today = w.high.iloc[-1], w.low.iloc[-1]
    ok = hi.notna() & hi_today.notna() & lo.notna() & lo_today.notna()
    return Health(session=w.dates[-1], above50=a50, above200=a200, n50=n50, n200=n200,
                  new_highs=int(((hi_today >= hi) & ok).sum()), new_lows=int(((lo_today <= lo) & ok).sum()),
                  n_range=int(ok.sum()), ref_sessions=ref_sessions)


def value_ago(series: pd.Series, k: int) -> Optional[float]:
    """The series value k sessions before the last one, or None when the history is shorter."""
    if len(series) <= k or pd.isna(series.iloc[-1 - k]):
        return None
    return float(series.iloc[-1 - k])


# ------------------------------------------------------------------ last-session table (P4, P5, P6)
def last_session_table(w: Wide) -> pd.DataFrame:
    """One row per stock priced on the last session: close, ret1_pct, gap_pct (open vs previous close), rvol, dv20, and the 20-week /
    52-week highs with the distance below them. Missing ingredients stay NaN."""
    close, prev = w.close.iloc[-1], w.close.iloc[-2]
    vol_med = w.volume.iloc[-51:-1].median()
    hi100 = w.high.rolling(WEEKS20_SESSIONS, min_periods=WEEKS20_SESSIONS).max().iloc[-1]
    hi252 = w.high.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).max().iloc[-1]
    t = pd.DataFrame({
        "close": close, "prev_close": prev,
        "ret1_pct": (close / prev - 1) * 100,
        "gap_pct": (w.open.iloc[-1] / prev - 1) * 100,
        "rvol": (w.volume.iloc[-1] / vol_med.where(vol_med > 0)),
        "dv20": (w.close * w.volume).iloc[-21:-1].mean(),
        "hi100": hi100, "hi252": hi252,
    })
    t = t[t["close"].notna()]                      # a missing prior bar blanks the 1-day figures only; the stock still counts for range measures
    t.loc[~(t["prev_close"] > 0), ["ret1_pct", "gap_pct"]] = np.nan
    t["below_hi100_pct"] = (t["hi100"] - t["close"]) / t["close"] * 100
    t["below_hi252_pct"] = (t["hi252"] - t["close"]) / t["close"] * 100
    return t


def gap_lists(t: pd.DataFrame, threshold: float = 5.0, top: int = 5, min_dv: float = MIN_DV_LISTS) -> Dict:
    pool = t[(t["dv20"] >= min_dv) & t["gap_pct"].notna() & t["ret1_pct"].notna()]
    ups, downs = pool[pool["gap_pct"] >= threshold], pool[pool["gap_pct"] <= -threshold]
    return {"threshold": threshold, "n_up": len(ups), "n_down": len(downs), "n_pool": len(pool),
            "ups": _rows(ups.sort_values(["gap_pct"], ascending=False).head(top)),
            "downs": _rows(downs.sort_values(["gap_pct"]).head(top))}


def near_high_list(t: pd.DataFrame, within_pct: float = 2.0, min_rvol: float = 2.0, top: int = 5, min_dv: float = MIN_DV_LISTS) -> Dict:
    pool = t[(t["dv20"] >= min_dv) & t["below_hi252_pct"].notna() & t["rvol"].notna() & t["ret1_pct"].notna()]
    hit = pool[(pool["below_hi252_pct"] <= within_pct) & (pool["rvol"] >= min_rvol)]
    return {"within_pct": within_pct, "min_rvol": min_rvol, "n": len(hit),
            "rows": _rows(hit.sort_values("rvol", ascending=False).head(top))}


def aligned_breakouts(t: pd.DataFrame, breakout_symbols: Sequence[str], within_pct: float = NEAR_PCT) -> Dict:
    """Of today's breakouts: how many also closed within `within_pct` of their 20-week high / 52-week high. Stocks without enough
    history for a range are counted separately (`n_short_*`), never assumed to qualify."""
    b = t.loc[t.index.intersection(list(breakout_symbols))]
    has_w, has_y = b["below_hi100_pct"].notna(), b["below_hi252_pct"].notna()
    near_w = has_w & (b["below_hi100_pct"] <= within_pct)
    near_y = has_y & (b["below_hi252_pct"] <= within_pct)
    return {"n_breakouts": len(breakout_symbols), "n_priced": len(b), "within_pct": within_pct,
            "n_week": int(near_w.sum()), "n_year": int(near_y.sum()), "n_both": int((near_w & near_y).sum()),
            "n_short_week": int((~has_w).sum()), "n_short_year": int((~has_y).sum()),
            "symbols": sorted(b.index[near_w & near_y]), "week_only": sorted(b.index[near_w & ~near_y]),
            "year_only": sorted(b.index[near_y & ~near_w]),
            "table": b.assign(near_week=near_w, near_year=near_y)}


def _rows(frame: pd.DataFrame) -> List[Dict]:
    out = []
    for sym, r in frame.iterrows():
        d = {k: (None if pd.isna(v) else float(v)) for k, v in r.items() if isinstance(v, (int, float, np.floating, np.integer))}
        d["symbol"] = sym
        out.append(d)
    return out


# ------------------------------------------------------------------ sectors (P2, P8)
def sector_changes(w: Wide, sector_of: Dict[str, str], sessions: int = 20,
                   min_stocks: int = MIN_STOCKS_PER_SECTOR) -> Tuple[List[Tuple[str, float, int]], int]:
    """Median % change over the last `sessions` sessions per sector (a median, so one 200% micro cap cannot move a sector), for the stocks
    with both endpoints. Returns ([(sector, median_pct, n_stocks)] best -> worst, number of stocks without a sector)."""
    if len(w.close) <= sessions:
        return [], 0
    chg = (w.close.iloc[-1] / w.close.iloc[-1 - sessions] - 1) * 100
    chg = chg.dropna()
    by: Dict[str, List[float]] = {}
    unclassified = 0
    for sym, v in chg.items():
        sec = sector_of.get(sym)
        if not sec or sec == "Unknown":
            unclassified += 1
            continue
        by.setdefault(sec, []).append(float(v))
    rows = [(s, float(np.median(v)), len(v)) for s, v in by.items() if len(v) >= min_stocks]
    return sorted(rows, key=lambda r: -r[1]), unclassified


# ------------------------------------------------------------------ calendar caveat
def is_monthly_options_expiry(d) -> bool:
    """Third Friday of the month: monthly options expiry (quarterly in Mar/Jun/Sep/Dec), a day on which volume is inflated across the board."""
    d = pd.Timestamp(d)
    return d.weekday() == 4 and 15 <= d.day <= 21


def is_quarterly_expiry(d) -> bool:
    d = pd.Timestamp(d)
    return is_monthly_options_expiry(d) and d.month in (3, 6, 9, 12)
