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
