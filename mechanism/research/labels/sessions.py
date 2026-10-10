"""The trading-session calendar fwd_v1 counts horizons on.

Derivation (no network, no wall clock): a date is a session when it is a weekday and at least COVERAGE_FRACTION of the
median daily symbol count has a stock_prices bar on it. It is then validated (strictly increasing, no gap that implies a
missing session) and cross-checked against the S&P 500 index series: an index bar on a date the stock-derived calendar
lacks means a session was lost, which would silently shift every later horizon, so the run fails closed.

Residual risk (documented in docs/research/FWD_V1_FORWARD_OUTCOME_ENGINE.md): a session missing from BOTH the stock and the
index data, on a date that does not make a >4-day gap, is undetectable from the database alone. `authoritative` lets a
caller pass an independent calendar (e.g. the Alpaca one in shared.market_calendar) that must agree exactly.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional, Tuple

from research.labels.fwd_v1 import CalendarError, cross_check_calendar, validate_calendar

COVERAGE_FRACTION = 0.5
SOURCE_DERIVED = "derived:stock_prices"
SOURCE_DERIVED_CROSSCHECKED = "derived:stock_prices+authoritative"


def derive_sessions(conn, start: date, end: date, benchmark_symbol: str = "^GSPC",
                    authoritative: Optional[Mapping[date, Any]] = None) -> Tuple[Tuple[date, ...], str]:
    cur = conn.cursor()
    cur.execute("SELECT date, COUNT(DISTINCT symbol) FROM stock_prices WHERE date BETWEEN %s AND %s "
                "AND EXTRACT(ISODOW FROM date) < 6 GROUP BY date ORDER BY date", (start, end))
    counts = cur.fetchall()
    cur.execute("SELECT date FROM market_index_prices WHERE symbol = %s AND date BETWEEN %s AND %s "
                "AND EXTRACT(ISODOW FROM date) < 6 ORDER BY date", (benchmark_symbol, start, end))
    index_dates = {r[0] for r in cur.fetchall()}
    conn.commit()
    if not counts:
        raise CalendarError(f"no stock_prices bars between {start} and {end}")
    ordered = sorted(c for _, c in counts)
    median = ordered[len(ordered) // 2]
    threshold = max(1, COVERAGE_FRACTION * median)
    sessions = validate_calendar([d for d, c in counts if c >= threshold])
    lost = sorted(d for d in index_dates if d not in set(sessions))
    if lost:
        raise CalendarError(f"{benchmark_symbol} has bars on {lost[:5]} which the stock-derived calendar lacks "
                            f"(thin or missing stock coverage): refusing to count horizons on it")
    source = SOURCE_DERIVED
    if authoritative:
        cross_check_calendar(sessions, authoritative)
        source = SOURCE_DERIVED_CROSSCHECKED
    return sessions, source
