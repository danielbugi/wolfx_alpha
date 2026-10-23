# File: backend/services/earnings_current.py
"""Which `earnings_calendar` rows are still CURRENT -- one rule, used by every backend reader of upcoming/last report dates.

Why this exists. `mechanism/data_updaters/earnings_calendar_updater.py` upserts each vendor answer on (symbol, report_date)
and never removes a row. When a company moves its report, the new date is inserted and the old one stays forever, so
"the first report_date >= today" can be a date the vendor no longer lists (production 2026-10-21: 274 symbols carried two
upcoming dates; in 274 of 274 exactly one came from the symbol's latest fetch; 132 boards showed the stale, earlier one).

The rule (nothing is deleted or rewritten; historical rows are never hidden):
  * latest fetch    = the rows whose fetched_at is within FETCH_RUN_WINDOW of the symbol's newest fetched_at. One updater
                      run writes all of a symbol's rows within milliseconds; runs for one symbol are hours to days apart.
  * complete        = the latest fetch contains at least one date on or after its own fetch day (New York) -- it really
                      answered "what is next" -- AND it wrote at least 2 rows or every row the symbol has. A failed fetch
                      writes nothing (so it never becomes "latest"); a partial one that lost the upcoming row is not complete.
  * superseded      = latest fetch complete AND the row is NOT in it AND the row has no reported EPS AND its date lies inside
                      the window that fetch answered for (on or after the earliest date the fetch returned). The vendor
                      answered for that period and no longer lists the date, and no report ever happened on it.
  * current         = not superseded. Rows with a reported EPS are always current (they are history); rows older than the
                      vendor's rolling window stay current; nothing is superseded on the evidence of an incomplete fetch.

`EARNINGS_CURRENT_CTES` is the SQL form (CTE definitions to splice into a WITH clause; it exposes `earnings_current`).
`classify` is the same rule in Python -- the reference the tests check the SQL against.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

FETCH_RUN_WINDOW = timedelta(minutes=15)
FETCH_DAY_TZ = ZoneInfo("America/New_York")

EARNINGS_CURRENT_CTES = """
earnings_latest_fetch AS (
    SELECT symbol, MAX(fetched_at) AS latest_fetch_at FROM earnings_calendar GROUP BY symbol
),
earnings_tagged AS (
    SELECT e.symbol, e.report_date, e.eps_actual, e.surprise_pct, e.fetched_at, lf.latest_fetch_at,
           e.fetched_at >= lf.latest_fetch_at - INTERVAL '15 minutes' AS in_latest_fetch
    FROM earnings_calendar e JOIN earnings_latest_fetch lf USING (symbol)
),
earnings_fetch_summary AS (
    SELECT symbol,
           COUNT(*) FILTER (WHERE in_latest_fetch) AS n_latest,
           COUNT(*) AS n_all,
           COALESCE(BOOL_OR(in_latest_fetch AND report_date >= (latest_fetch_at AT TIME ZONE 'America/New_York')::date), false)
               AS latest_has_upcoming,
           MIN(report_date) FILTER (WHERE in_latest_fetch) AS latest_window_start
    FROM earnings_tagged GROUP BY symbol
),
earnings_current AS (
    SELECT t.symbol, t.report_date, t.eps_actual, t.surprise_pct, t.fetched_at, t.latest_fetch_at, t.in_latest_fetch,
           (s.latest_has_upcoming AND (s.n_latest >= 2 OR s.n_latest = s.n_all)) AS latest_fetch_complete,
           NOT ((s.latest_has_upcoming AND (s.n_latest >= 2 OR s.n_latest = s.n_all))
                AND NOT t.in_latest_fetch AND t.eps_actual IS NULL AND t.report_date >= s.latest_window_start) AS is_current
    FROM earnings_tagged t JOIN earnings_fetch_summary s USING (symbol)
)"""


@dataclass(frozen=True)
class RowState:
    in_latest_fetch: bool
    latest_fetch_complete: bool
    is_current: bool
    latest_fetch_at: datetime


def classify(rows: Iterable[Mapping]) -> Dict[Tuple[str, date], RowState]:
    """Python reference of EARNINGS_CURRENT_CTES. `rows`: symbol, report_date (date), eps_actual (None = not reported),
    fetched_at (timezone-aware datetime). Returns {(symbol, report_date): RowState}."""
    by_sym: Dict[str, list] = {}
    for r in rows:
        if r["fetched_at"].tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        by_sym.setdefault(r["symbol"], []).append(r)
    out: Dict[Tuple[str, date], RowState] = {}
    for sym, rs in by_sym.items():
        latest = max(r["fetched_at"] for r in rs)
        fetch_day = latest.astimezone(FETCH_DAY_TZ).date()
        in_latest = {id(r): r["fetched_at"] >= latest - FETCH_RUN_WINDOW for r in rs}
        latest_rows = [r for r in rs if in_latest[id(r)]]
        has_upcoming = any(r["report_date"] >= fetch_day for r in latest_rows)
        complete = has_upcoming and (len(latest_rows) >= 2 or len(latest_rows) == len(rs))
        window_start = min(r["report_date"] for r in latest_rows)
        for r in rs:
            superseded = (complete and not in_latest[id(r)] and r.get("eps_actual") is None
                          and r["report_date"] >= window_start)
            out[(sym, r["report_date"])] = RowState(in_latest[id(r)], complete, not superseded, latest)
    return out


def next_current_report(rows: Iterable[Mapping], symbol: str, on_or_after: date) -> Optional[date]:
    """First current report date on or after `on_or_after` for one symbol (what the Momentum Board shows)."""
    rows = list(rows)
    st = classify(rows)
    dates = sorted(r["report_date"] for r in rows if r["symbol"] == symbol and r["report_date"] >= on_or_after
                   and st[(symbol, r["report_date"])].is_current)
    return dates[0] if dates else None
