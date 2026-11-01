"""Read-only SEC ingestion coverage and data-quality report: one JSON document from SQL over the SEC tables, the price universe and the yfinance fundamentals.

    python mechanism/data_updaters/sec_fundamentals_updater.py --coverage

What it answers: how much of the live universe has SEC data (by status, sector and reporting period), how recent the newest filing is, which financial fields are missing, how SEC compares with
yfinance for freshness, and what is failing or still waiting. US domestic filers only are expected: foreign private issuers file 20-F / 40-F (no 10-Q, often IFRS), so `no_xbrl` / `not_found` /
`no_filings` are reported as a separate group and never counted as failures.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List

UNIVERSE = "SELECT DISTINCT symbol FROM stock_prices WHERE symbol NOT IN (SELECT symbol FROM inactive_symbols)"


def _s(v: Any) -> Any:
    return str(v) if isinstance(v, (date, datetime)) else v


def _rows(db, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [{k: _s(v) for k, v in r.items()} for r in db.execute_dict_query(sql, params)]


def coverage(db) -> Dict[str, Any]:
    out: Dict[str, Any] = {"schema": "sec_coverage_v1"}
    out["universe"] = _rows(db, f"""
        WITH u AS ({UNIVERSE}),
             m AS (SELECT u.symbol, c.cik FROM u LEFT JOIN sec_symbol_cik c ON c.symbol = u.symbol),
             q AS (SELECT DISTINCT cik FROM sec_quarter WHERE metric = 'revenue')
        SELECT COUNT(*) AS symbols,
               COUNT(*) FILTER (WHERE m.cik IS NOT NULL) AS with_cik,
               COUNT(*) FILTER (WHERE m.cik IS NULL) AS without_cik,
               COUNT(st.cik) AS checked,
               COUNT(*) FILTER (WHERE m.cik IS NOT NULL AND st.cik IS NULL) AS never_checked,
               COUNT(*) FILTER (WHERE st.last_status = 'ok') AS status_ok,
               COUNT(*) FILTER (WHERE st.last_status IN ('no_xbrl', 'not_found', 'no_filings')) AS no_us_gaap_periodic_filings,
               COUNT(*) FILTER (WHERE st.last_status = 'error') AS status_error,
               COUNT(q.cik) AS with_revenue_quarters
        FROM m LEFT JOIN sec_fundamentals_state st ON st.cik = m.cik LEFT JOIN q ON q.cik = m.cik""")[0]
    out["no_cik_sample"] = [r["symbol"] for r in _rows(db, f"SELECT symbol FROM ({UNIVERSE}) u WHERE symbol NOT IN (SELECT symbol FROM sec_symbol_cik) ORDER BY symbol LIMIT 25")]
    out["by_sector"] = _rows(db, f"""
        WITH u AS ({UNIVERSE}),
             sec AS (SELECT DISTINCT ON (symbol) symbol, COALESCE(NULLIF(sector, ''), 'Unknown') AS sector FROM daily_fundamentals WHERE date >= CURRENT_DATE - 45 ORDER BY symbol, date DESC),
             has AS (SELECT DISTINCT c.symbol FROM sec_symbol_cik c JOIN sec_quarter q ON q.cik = c.cik WHERE q.metric = 'revenue')
        SELECT COALESCE(sec.sector, 'Unknown') AS sector, COUNT(*) AS symbols, COUNT(has.symbol) AS with_sec_quarters,
               ROUND(100.0 * COUNT(has.symbol) / COUNT(*), 1) AS pct
        FROM u LEFT JOIN sec ON sec.symbol = u.symbol LEFT JOIN has ON has.symbol = u.symbol GROUP BY 1 ORDER BY symbols DESC""")
    out["by_reporting_period"] = _rows(db, """
        SELECT to_char(date_trunc('quarter', period_end), 'YYYY"Q"Q') AS period, COUNT(DISTINCT cik) AS companies, COUNT(*) AS rows
        FROM sec_quarter WHERE metric = 'revenue' AND period_end >= CURRENT_DATE - 1100 GROUP BY 1 ORDER BY 1 DESC""")
    out["latest_filing"] = _rows(db, """
        SELECT MAX(newest_periodic_filed) AS newest_filing_date, MIN(newest_periodic_filed) AS oldest_newest_filing,
               COUNT(*) FILTER (WHERE newest_periodic_filed >= CURRENT_DATE - 100) AS within_100d,
               COUNT(*) FILTER (WHERE newest_periodic_filed < CURRENT_DATE - 100 AND newest_periodic_filed >= CURRENT_DATE - 200) AS between_100_200d,
               COUNT(*) FILTER (WHERE newest_periodic_filed < CURRENT_DATE - 200) AS older_than_200d
        FROM sec_fundamentals_state WHERE last_status = 'ok'""")[0]
    out["missing_fields_recent_quarters"] = _rows(db, """
        WITH recent AS (SELECT DISTINCT cik, period_end FROM sec_quarter WHERE metric = 'revenue' AND period_end >= CURRENT_DATE - 400)
        SELECT m.metric, COUNT(*) FILTER (WHERE q.cik IS NULL) AS missing, COUNT(*) AS of_company_quarters,
               ROUND(100.0 * COUNT(*) FILTER (WHERE q.cik IS NULL) / NULLIF(COUNT(*), 0), 1) AS pct_missing
        FROM recent r CROSS JOIN (VALUES ('operating_income'), ('net_income'), ('operating_cash_flow'), ('free_cash_flow'), ('gross_profit'), ('eps_diluted')) m(metric)
             LEFT JOIN sec_quarter q ON q.cik = r.cik AND q.period_end = r.period_end AND q.metric = m.metric
        GROUP BY m.metric ORDER BY pct_missing DESC NULLS LAST""")
    out["freshness_vs_yfinance"] = _rows(db, """
        WITH s AS (SELECT c.symbol, MAX(q.period_end) AS sec_q FROM sec_quarter q JOIN sec_symbol_cik c ON c.cik = q.cik WHERE q.metric = 'revenue' GROUP BY 1),
             y AS (SELECT symbol, MAX(quarter) AS yf_q FROM quarterly_fundamentals GROUP BY 1)
        SELECT COUNT(*) FILTER (WHERE s.sec_q > y.yf_q + 5) AS sec_newer, COUNT(*) FILTER (WHERE ABS(s.sec_q - y.yf_q) <= 5) AS same_quarter,
               COUNT(*) FILTER (WHERE s.sec_q < y.yf_q - 5) AS yfinance_newer, COUNT(*) AS both_sources,
               (SELECT COUNT(*) FROM y WHERE symbol NOT IN (SELECT symbol FROM s)) AS yfinance_only
        FROM s JOIN y USING (symbol)""")[0]
    out["failures"] = {
        "error_symbols": _rows(db, "SELECT COUNT(*) AS n FROM sec_fundamentals_state WHERE last_status = 'error'")[0]["n"],
        "top_errors": _rows(db, "SELECT LEFT(COALESCE(last_error, ''), 120) AS error, COUNT(*) AS n FROM sec_fundamentals_state WHERE last_status = 'error' GROUP BY 1 ORDER BY n DESC LIMIT 8"),
        "oldest_error_checked_at": _rows(db, "SELECT MIN(last_checked_at) AS t FROM sec_fundamentals_state WHERE last_status = 'error'")[0]["t"],
    }
    out["generated_from"] = "database"
    return out
