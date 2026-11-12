"""Opportunity Intelligence v0: the read-only repository. Every read runs on a READ ONLY session (a write raises), and every query is bounded
by the explicit session; the knowledge-cutoff filters are applied in brief.py, on the timestamps returned here.

Reads: fundamental_inflection_event, fundamental_feature, sec_filing, companies, earnings_calendar, price_discontinuities, stock_prices, market_index_prices
(its own small price queries: the fwd_v1 label engine stays import-isolated, test_import_separation)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Dict, Iterator, List, Sequence

from psycopg2.extras import RealDictCursor


METHOD = "inflection_v2_sec_pit"
BENCHMARK = "^GSPC"

EVENTS_SQL = r"""
SELECT e.id, e.symbol, e.quarter_end, e.event_type, e.severity, e.available_at, e.availability_basis, e.first_detected_at, e.updated_at,
       e.revision_count, e.explanation, f.metrics AS feature_metrics, f.computed_at AS feature_computed_at,
       q.acc AS accession, sf.form, sf.accepted_at, sf.filed_date, co.company_name
  FROM fundamental_inflection_event e
  JOIN fundamental_feature f ON f.method_version = e.method_version AND f.symbol = e.symbol AND f.quarter_end = e.quarter_end
  LEFT JOIN LATERAL (SELECT substring(n FROM '^prov:q=(\d{10}-\d{2}-\d{6})$') AS acc
                       FROM jsonb_array_elements_text(e.quality_notes) n WHERE n LIKE 'prov:q=%%' LIMIT 1) q ON true
  LEFT JOIN sec_filing sf ON sf.accession = q.acc
  LEFT JOIN LATERAL (SELECT c.company_name FROM companies c WHERE c.symbol = e.symbol LIMIT 1) co ON true
 WHERE e.method_version = %s AND e.event_type = ANY(%s) AND e.available_at BETWEEN %s AND %s
 ORDER BY e.symbol, e.quarter_end, e.event_type
"""

BARS_SQL = "SELECT symbol, date, close FROM stock_prices WHERE symbol = ANY(%s) AND date BETWEEN %s AND %s AND close IS NOT NULL ORDER BY symbol, date"
BENCH_SQL = "SELECT date, close FROM market_index_prices WHERE symbol = %s AND date BETWEEN %s AND %s AND close IS NOT NULL ORDER BY date"

DISCONTINUITY_SQL = """
SELECT symbol, date, kind, ratio, detected_at FROM price_discontinuities WHERE symbol = ANY(%s) AND date BETWEEN %s AND %s ORDER BY symbol, date
"""

EARNINGS_SQL = """
SELECT symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at
  FROM earnings_calendar WHERE symbol = ANY(%s) AND report_date BETWEEN %s AND %s ORDER BY symbol, report_date
"""


@contextmanager
def read_only(conn) -> Iterator[Any]:
    """The connection in a READ ONLY session for the duration; restored (and rolled back) afterwards, so a pooled connection is unchanged."""
    prev = conn.readonly
    conn.rollback()
    conn.readonly = True
    try:
        yield conn
    finally:
        conn.rollback()
        conn.readonly = prev


def _rows(conn, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows


def load(conn, session: date, start: date, headline_types: Sequence[str], price_start: date) -> Dict[str, Any]:
    """Everything one brief run needs, read once. `start`..`session` bounds the public date of the events."""
    with read_only(conn):
        events = _rows(conn, EVENTS_SQL, (METHOD, list(headline_types), start, session))
        symbols = sorted({e["symbol"] for e in events})
        earnings = _rows(conn, EARNINGS_SQL, (symbols, price_start, session)) if symbols else []
        bench = {r["date"]: float(r["close"]) for r in _rows(conn, BENCH_SQL, (BENCHMARK, price_start, session))}
        bars: Dict[str, Dict[date, float]] = {}
        for r in (_rows(conn, BARS_SQL, (symbols, price_start, session)) if symbols else []):
            bars.setdefault(r["symbol"], {})[r["date"]] = float(r["close"])
        disc = _rows(conn, DISCONTINUITY_SQL, (symbols, price_start, session)) if symbols else []
    # Sessions = the S&P 500's trading days (weekdays with an index close). A session missing from the index would also lack the benchmark
    # close the market response needs, so that window is 'not available' rather than silently shifted.
    return {"events": events, "earnings": earnings, "discontinuities": disc, "pre_release": {}, "sessions": sorted(d for d in bench if d.weekday() < 5),
            "calendar_source": "market_index_prices:^GSPC", "bars": bars, "bench": bench}


def latest_session(conn) -> date:
    """The newest session with an S&P 500 close in the database (data-defined, never the wall clock)."""
    with read_only(conn):
        cur = conn.cursor()
        cur.execute("SELECT max(date) FROM market_index_prices WHERE symbol = %s", (BENCHMARK,))
        d = cur.fetchone()[0]
    if d is None:
        raise LookupError("no benchmark closes in the database")
    return d
