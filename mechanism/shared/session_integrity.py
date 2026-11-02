"""Session-identity checks shared by the screener and the signal-ledger writer.

A record (signal / price row) belongs to exactly one US market session. When a job is processing session
T, a record dated anything else is rejected with a reason -- it is never relabelled to T. Stdlib only, so it
is testable without a database."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

STALE_BAR = "stale_bar"            # record dated BEFORE the session being processed
FUTURE_BAR = "future_bar"          # record dated AFTER it
UNREADABLE_DATE = "unreadable_date"


def to_date(value: Any) -> Optional[date]:
    """date | datetime | ISO string -> date; None when it cannot be read (never guessed)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def classify(record_date: Any, session: date) -> Optional[str]:
    """None when the record belongs to `session`, otherwise the rejection reason."""
    d = to_date(record_date)
    if d is None:
        return UNREADABLE_DATE
    if d < session:
        return STALE_BAR
    if d > session:
        return FUTURE_BAR
    return None


def partition_by_session(records: Iterable[Dict], session: date,
                         date_key: str = "screening_date") -> Tuple[List[Dict], List[Tuple[Dict, str]]]:
    """-> (accepted, [(record, reason), ...]). Order preserved; accepted records are returned untouched."""
    accepted: List[Dict] = []
    rejected: List[Tuple[Dict, str]] = []
    for rec in records:
        reason = classify(rec.get(date_key), session)
        if reason is None:
            accepted.append(rec)
        else:
            rejected.append((rec, reason))
    return accepted, rejected


# ------------------------------------------------------------------------------------------------------------ price bars at write time
# A price bar is stored only for a real US session. The production row that motivated this: CBL on 2026-02-16 (Presidents' Day), a vendor filler
# bar (open = high = low = close = previous close, volume 0) written by a backfill; every reader that builds a cross-symbol date axis then saw a
# "session" on which ~3,000 stocks had no price (a 200-session mean breaks for 200 sessions). Readers that derive sessions from stock_prices
# mostly guard with a coverage threshold, but the right place is the writer.
WEEKEND = "weekend"
NOT_A_SESSION = "not_a_session"    # a weekday the exchange calendar says was closed (holiday)
INVALID_OHLC = "invalid_ohlc"      # missing / non-positive price, low > high, open or close outside [low, high]


def bar_rejection(record_date: Any, o: Any, h: Any, l: Any, c: Any, sessions: Optional[Iterable[date]],
                  calendar_span: Optional[Tuple[date, date]] = None) -> Optional[str]:
    """None when the bar may be stored, otherwise the reason. `sessions` = the exchange's sessions covering `calendar_span` (inclusive), or None
    when no calendar is available: then only weekends are rejected (fail open, like market_calendar: a wrongly dropped real bar loses data that
    no later run restores, a kept holiday bar is caught by the readers' coverage checks). A date outside `calendar_span` is judged by weekday only."""
    d = to_date(record_date)
    if d is None:
        return UNREADABLE_DATE
    vals = []
    for v in (o, h, l, c):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return INVALID_OHLC
        if x != x or x <= 0:                                       # NaN or non-positive
            return INVALID_OHLC
        vals.append(x)
    o_, h_, l_, c_ = vals
    if l_ > h_ or not (l_ <= c_ <= h_) or not (l_ <= o_ <= h_):
        return INVALID_OHLC
    if d.weekday() >= 5:
        return WEEKEND
    if sessions is not None and calendar_span is not None and calendar_span[0] <= d <= calendar_span[1] and d not in sessions:
        return NOT_A_SESSION
    return None


def is_flat_zero_volume(o: Any, h: Any, l: Any, c: Any, volume: Any) -> bool:
    """open = high = low = close and no volume: a no-trade day (halt, illiquid name) or vendor filler. Counted, not rejected: whether such bars
    belong in stock_prices is a separate decision (they are real for a halted stock)."""
    try:
        return float(volume or 0) == 0 and float(o) == float(h) == float(l) == float(c)
    except (TypeError, ValueError):
        return False


def screen_price_bars(rows: Iterable[Dict], sessions: Optional[Iterable[date]], calendar_span: Optional[Tuple[date, date]] = None,
                      date_key: str = "date") -> Tuple[List[Dict], List[Tuple[Dict, str]], int]:
    """-> (accepted, [(row, reason), ...], number of accepted flat zero-volume bars). Rows carry date/open/high/low/close/volume. Order preserved."""
    sess = set(sessions) if sessions is not None else None
    accepted: List[Dict] = []
    rejected: List[Tuple[Dict, str]] = []
    flat = 0
    for r in rows:
        reason = bar_rejection(r.get(date_key), r.get("open"), r.get("high"), r.get("low"), r.get("close"), sess, calendar_span)
        if reason is None:
            accepted.append(r)
            flat += is_flat_zero_volume(r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("volume"))
        else:
            rejected.append((r, reason))
    return accepted, rejected, flat


def summarize_rejections(rejected: List[Tuple[Dict, str]], limit: int = 8,
                         symbol_key: str = "symbol") -> str:
    """'stale_bar=312 (AAPL, MSFT, ...)' -- one log-friendly line."""
    if not rejected:
        return "none"
    by_reason: Dict[str, List[str]] = {}
    for rec, reason in rejected:
        by_reason.setdefault(reason, []).append(str(rec.get(symbol_key, "?")))
    return "; ".join(f"{r}={len(s)} ({', '.join(s[:limit])}{', ...' if len(s) > limit else ''})"
                     for r, s in by_reason.items())
