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

The summary's numbers come from mechanism/strategy_analytics (via StrategyIntelligenceService) -- the
same canonical definitions the Strategy Intelligence API and the Telegram assistant use. This service
only presents that result in the track record's own, stricter way: nothing below MIN_SAMPLE_SIZE is
shown at all (Strategy Intelligence shows it as 'preliminary'). A win is a target reached before the
stop; a positive-R expiry is not a win.
"""
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services.strategy_intelligence_service import StrategyIntelligenceService, definitions

MIN_SAMPLE_SIZE = definitions.MIN_SAMPLE_SIZE  # fewer resolved signals than this and a win rate/avg R is noise


class TrackRecordService:
    """Reads signal_ledger for the authenticated dashboard's track-record view."""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def get_summary(self) -> Dict[str, Any]:
        """Every strategy's resolved signals, plus the still-open count reported separately. Returns
        suppressed=True with no stats unless the canonical win rate is state 'ok' (>= MIN_SAMPLE_SIZE
        resolved) -- never a win rate computed off too few rows."""
        result = StrategyIntelligenceService(self.get_db_connection).overall_performance(None)
        counts, perf = result["counts"], result["performance"]
        summary: Dict[str, Any] = {
            "resolved_count": counts["resolved"],
            "open_count": counts["open_total"],
            "earliest_signal_date": result["first_session"],
            "latest_signal_date": result["latest_session"],
            "by_status": result["by_status"],
            "min_sample_size": MIN_SAMPLE_SIZE,
            "suppressed": perf["win_rate"]["state"] != definitions.STATE_OK,
        }
        if not summary["suppressed"]:
            summary["win_rate_pct"] = round(100.0 * perf["win_rate"]["value"], 1)
            summary["avg_outcome_r"] = round(perf["average_r"]["value"], 3)
        return summary

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
