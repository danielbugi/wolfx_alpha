# File: backend/services/track_record_service.py
"""
Track Record Service - reads signal_ledger (mechanism/add_signal_ledger_tables.sql), the live
outcome ledger every daily breakout signal is written to and walked forward by
mechanism/screeners/evaluate_signal_ledger.py.

Honesty discipline, matching mechanism/alerts/scoreboard.py: suppress any cohort below a minimum
sample size rather than reporting a misleadingly precise win rate off a handful of rows, and always
report closed vs. still-open counts separately -- never blend an in-progress signal into a win rate
as though it had already lost or won.

Authenticated only for now (see backend/routers/track_record.py) -- a public summary is a deliberate,
separate follow-up once the numbers and copy have been validated internally, not this pass.
"""
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

MIN_SAMPLE_SIZE = 5  # fewer resolved signals than this and a win rate/avg R is noise, not a stat


class TrackRecordService:
    """Reads signal_ledger for the authenticated dashboard's track-record view."""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def get_summary(self) -> Dict[str, Any]:
        """Aggregate stats over every resolved (non-'open') signal, plus the still-open count
        reported separately. Returns suppressed=True with no stats when the resolved sample is
        below MIN_SAMPLE_SIZE -- never a win rate computed off too few rows."""
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status != 'open') AS resolved_count,
                    COUNT(*) FILTER (WHERE status = 'open') AS open_count,
                    COUNT(*) FILTER (WHERE status != 'open' AND outcome_r > 0) AS resolved_positive,
                    AVG(outcome_r) FILTER (WHERE status != 'open') AS avg_outcome_r,
                    MIN(signal_date) AS earliest_signal_date,
                    MAX(signal_date) AS latest_signal_date
                FROM signal_ledger
            """)
            row = cursor.fetchone()

            cursor.execute("""
                SELECT status, COUNT(*) AS n
                FROM signal_ledger
                WHERE status != 'open'
                GROUP BY status
            """)
            by_status = {r["status"]: r["n"] for r in cursor.fetchall()}
            cursor.close()

            resolved_count = row["resolved_count"] or 0
            summary: Dict[str, Any] = {
                "resolved_count": resolved_count,
                "open_count": row["open_count"] or 0,
                "earliest_signal_date": row["earliest_signal_date"],
                "latest_signal_date": row["latest_signal_date"],
                "by_status": by_status,
                "min_sample_size": MIN_SAMPLE_SIZE,
                "suppressed": resolved_count < MIN_SAMPLE_SIZE,
            }
            if not summary["suppressed"]:
                summary["win_rate_pct"] = round(100.0 * row["resolved_positive"] / resolved_count, 1)
                summary["avg_outcome_r"] = round(float(row["avg_outcome_r"]), 3)
            return summary
        finally:
            conn.close()

    def get_recent_signals(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Most recent signals, newest first. `status` filters to one status (including 'open');
        omitted returns every status."""
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            query = """
                SELECT symbol, signal_date, direction, entry_price, stop_price,
                       target1_price, target2_price, target3_price, sector, quality_grade,
                       status, outcome_r, mae_r, resolved_date, bars_held
                FROM signal_ledger
            """
            params: List[Any] = []
            if status:
                query += " WHERE status = %s"
                params.append(status)
            query += " ORDER BY signal_date DESC, symbol ASC LIMIT %s"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            cursor.close()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """Every ledger row for one symbol, newest first."""
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT symbol, signal_date, direction, entry_price, stop_price,
                       target1_price, target2_price, target3_price, sector, quality_grade,
                       status, outcome_r, mae_r, resolved_date, bars_held
                FROM signal_ledger
                WHERE symbol = %s
                ORDER BY signal_date DESC
            """, (symbol,))
            rows = cursor.fetchall()
            cursor.close()
            return [dict(r) for r in rows]
        finally:
            conn.close()
