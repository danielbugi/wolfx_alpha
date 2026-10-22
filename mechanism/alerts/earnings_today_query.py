# File: mechanism/alerts/earnings_today_query.py
"""The "who reports today" channel query (send_earnings_today.py), kept in a pure module so tests can run it on a scratch
database without importing the sender (which opens the shared connection pool)."""
from alerts.earnings_current import EARNINGS_CURRENT_CTES

# Only dates still CURRENT under the shared rule (alerts/earnings_current.py, identical to the backend's): a
# rescheduled report leaves its old date in earnings_calendar forever, and posting "reports today" for it announced
# names on the wrong day (2026-09-23..10-21: 62 stale mentions in 19 posts). The CTE is aggregated once and
# hash-joined -- never a per-row LATERAL into earnings_current (2026-10-22 lesson).
TODAYS_REPORTERS_SQL = """
    WITH """ + EARNINGS_CURRENT_CTES + """,
    todays_current AS (
        SELECT symbol, report_date FROM earnings_current WHERE report_date = %s AND is_current
    )
    SELECT ec.symbol, ec.eps_estimate, df.sector, sp.close
    FROM todays_current tc
    JOIN earnings_calendar ec ON ec.symbol = tc.symbol AND ec.report_date = tc.report_date
    JOIN (SELECT DISTINCT symbol FROM digest_stocks
          WHERE session_date = (SELECT MAX(session_date) FROM digest_stocks)) cov
        ON cov.symbol = ec.symbol
    LEFT JOIN LATERAL (
        SELECT sector FROM daily_fundamentals
        WHERE symbol = ec.symbol AND sector IS NOT NULL ORDER BY date DESC LIMIT 1
    ) df ON TRUE
    LEFT JOIN LATERAL (
        SELECT close FROM stock_prices WHERE symbol = ec.symbol ORDER BY date DESC LIMIT 1
    ) sp ON TRUE
    ORDER BY ec.symbol
"""
