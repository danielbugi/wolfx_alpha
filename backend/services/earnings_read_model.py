# File: backend/services/earnings_read_model.py
"""The ONE read model behind every product-facing earnings date (Stock page badge and Earnings tab, Watchlist chart
markers, Earnings page rows), built on the approved current-row rule (services/earnings_current.py, the same rule as
the Screener's and the board's next_earnings_date). Yahoo is never consulted live here: the dates are the stored
earnings_calendar rows the pipeline's updater wrote, read through the rule.

Per symbol it returns the current rows with an explicit status, never an invented one:
  * reported          -- a reported EPS is stored (history; always current under the rule).
  * upcoming          -- on or after `today` (New York date). `listed_by_latest_check` = listed by the symbol's latest fetch;
                         an upcoming date NOT listed by it is one an older fetch listed and the latest fetch neither listed nor
                         ruled out (an incomplete answer is not evidence of a cancellation -- e.g. META 2026-10-28:
                         listed 10-07, the 10-21 fetch returned no upcoming date at all).
  * awaiting_results  -- a past date with no reported EPS stored yet.
plus the provenance a page needs to say how fresh it is: source, rule, latest_fetch_at (when the provider was last
asked) and schedule_complete (whether that answer included an upcoming date). No report time (BMO/AMC) is stored, so
none is returned.

Query shape: ONE statement for any number of symbols. The approved CTEs are reused verbatim except that their two reads
of earnings_calendar are narrowed to the requested symbols -- the rule is evaluated per symbol, so the result for those
symbols is identical (tests/test_earnings_read_model.py checks it against the unfiltered CTEs)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.earnings_current import EARNINGS_CURRENT_CTES, FETCH_DAY_TZ

SOURCE = "earnings_calendar"
RULE = "current_row_v1"
MAX_SYMBOLS = 50
MAX_PAST = 40

_SCAN = "FROM earnings_calendar"
assert EARNINGS_CURRENT_CTES.count(_SCAN) == 2, "the approved CTEs changed shape: review the symbol narrowing below"
# earnings_latest_fetch and earnings_tagged both read earnings_calendar; earnings_tagged reads it as "earnings_calendar e".
FILTERED_CTES = (EARNINGS_CURRENT_CTES
                 .replace("FROM earnings_calendar GROUP BY symbol", "FROM earnings_calendar WHERE symbol = ANY(%(symbols)s) GROUP BY symbol")
                 .replace("FROM earnings_calendar e JOIN", "FROM (SELECT * FROM earnings_calendar WHERE symbol = ANY(%(symbols)s)) e JOIN"))
assert FILTERED_CTES.count("ANY(%(symbols)s)") == 2

READ_SQL = "WITH " + FILTERED_CTES + """
SELECT c.symbol, c.report_date, e.eps_estimate::float8, c.eps_actual::float8, c.surprise_pct::float8, c.fetched_at,
       c.latest_fetch_at, c.in_latest_fetch, c.latest_fetch_complete
FROM earnings_current c
JOIN earnings_calendar e ON e.symbol = c.symbol AND e.report_date = c.report_date
WHERE c.is_current
ORDER BY c.symbol, c.report_date"""


def ny_today(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(FETCH_DAY_TZ)).astimezone(FETCH_DAY_TZ).date()


def _iso(v: Any) -> Optional[str]:
    """Timestamps in UTC, whatever the database session's time zone (production's is Asia/Jerusalem)."""
    if v is None:
        return None
    return (v.astimezone(timezone.utc) if getattr(v, "tzinfo", None) else v).isoformat()


def assemble(rows: Sequence[Sequence[Any]], symbols: Sequence[str], today: date, past_limit: int) -> Dict[str, Dict[str, Any]]:
    """Pure: query rows -> one entry per requested symbol (a symbol without rows gets an explicit empty entry)."""
    by: Dict[str, List[Sequence[Any]]] = {s: [] for s in symbols}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        rs = by.get(sym, [])
        dates = []
        for (_, d, est, act, surp, fetched, latest, in_latest, complete) in rs:
            status = "reported" if act is not None else "upcoming" if d >= today else "awaiting_results"
            dates.append({"date": d.isoformat(), "eps_estimate": est, "eps_actual": act, "surprise_pct": surp, "status": status,
                          "listed_by_latest_check": bool(in_latest) if status == "upcoming" else None, "listed_at": _iso(fetched)})
        upcoming = [x for x in dates if x["status"] == "upcoming"]
        past = [x for x in dates if x["status"] != "upcoming"][-past_limit:] if past_limit else []
        nxt = upcoming[0] if upcoming else None
        out[sym] = {
            "symbol": sym,
            "source": SOURCE,
            "rule": RULE,
            "today": today.isoformat(),
            "latest_fetch_at": _iso(rs[0][6]) if rs else None,
            "schedule_complete": bool(rs[0][8]) if rs else None,
            "next": {"date": nxt["date"], "listed_by_latest_check": nxt["listed_by_latest_check"], "listed_at": nxt["listed_at"]} if nxt else None,
            "earnings_dates": past + upcoming,
        }
    return out


class EarningsReadModel:
    def __init__(self, get_connection: Callable):
        self.get_connection = get_connection

    def for_symbols(self, symbols: Sequence[str], past_limit: int = 12, today: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
        syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        if not syms:
            return {}
        if len(syms) > MAX_SYMBOLS:
            raise ValueError(f"at most {MAX_SYMBOLS} symbols")
        if not (0 <= past_limit <= MAX_PAST):
            raise ValueError(f"past_limit must be 0-{MAX_PAST}")
        conn = self.get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            conn.rollback()
            cur = conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(READ_SQL, {"symbols": syms})
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.rollback()
            conn.close()
        return assemble(rows, syms, today or ny_today(), past_limit)
