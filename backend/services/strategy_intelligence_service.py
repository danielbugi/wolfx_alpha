# File: backend/services/strategy_intelligence_service.py
"""
Strategy Intelligence - the backend side. Every number comes from mechanism/strategy_analytics
(the canonical calculation layer shared with the Telegram assistant, which runs in the mechanism image
and cannot import backend code); this module only binds it to the backend's connection pool.

Imports the mechanism package by path, appended so the backend's own `utils`/`services`/`routers`
keep winning -- the same pattern as telegram_control_service.py. strategy_analytics never imports
shared.database, so no second pool is opened.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from psycopg2.extras import RealDictCursor

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from strategy_analytics import analytics  # noqa: E402
from strategy_analytics import definitions  # noqa: E402
from strategy_analytics import research  # noqa: E402

ResearchUnavailable = research.ResearchUnavailable


class StrategyNotFound(LookupError):
    pass


class StrategyIntelligenceService:
    def __init__(self, get_connection: Callable):
        self._get_connection = get_connection

    @contextmanager
    def _fetch(self) -> Iterator[analytics.Fetch]:
        """One pooled connection per request, so every query of one response reads through the same
        connection; the pooled close() rolls back and returns it."""
        conn = self._get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            def fetch(sql: str, params: Any = None) -> List[Dict[str, Any]]:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, params)
                    return [dict(r) for r in cur.fetchall()]
            yield fetch
        finally:
            conn.close()

    @staticmethod
    def _strategy(fetch: analytics.Fetch, key: str, version: str) -> Dict[str, Any]:
        s = analytics.get_strategy(fetch, key, version)
        if s is None:
            raise StrategyNotFound(f"{key}/{version}")
        return s

    def definitions(self) -> Dict[str, Any]:
        return {
            "definitions_version": definitions.DEFINITIONS_VERSION,
            "definitions": definitions.DEFINITIONS,
            "min_sample_size": definitions.MIN_SAMPLE_SIZE,
            "stale_after_sessions": definitions.STALE_AFTER_SESSIONS,
            "statuses": list(definitions.ALL_STATUSES),
            "lifecycles": list(definitions.LIFECYCLES),
            "resolution_flags": list(definitions.RESOLUTION_FLAGS),
            "evaluation_flags": list(definitions.EVALUATION_FLAGS),
            "signal_sorts": list(analytics.SORTS),
            "capabilities": definitions.CAPABILITIES,
        }

    def list_strategies(self) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return {"strategies": analytics.list_strategies(fetch)}

    def summary(self, key: str, version: str) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return analytics.summary(fetch, self._strategy(fetch, key, version))

    def data_health(self, key: str, version: str) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return analytics.data_health(fetch, self._strategy(fetch, key, version))

    def signals(self, key: str, version: str, **filters: Any) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return analytics.list_signals(fetch, self._strategy(fetch, key, version), **filters)

    def signal(self, key: str, version: str, ledger_id: int) -> Optional[Dict[str, Any]]:
        with self._fetch() as fetch:
            return analytics.get_signal(fetch, self._strategy(fetch, key, version), ledger_id)

    # --- Release B research layer (read-only; every research table may be absent before migration 22) ---
    def research_summary(self, key: str, version: str) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return research.summary(fetch, self._strategy(fetch, key, version))

    def research_capture_runs(self, key: str, version: str, limit: int = research.HISTORY_DEFAULT) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return research.capture_runs(fetch, self._strategy(fetch, key, version), limit)

    def research_candidates(self, key: str, version: str, **filters: Any) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return research.list_candidates(fetch, self._strategy(fetch, key, version), **filters)

    def research_candidate(self, key: str, version: str, observation_id: int) -> Optional[Dict[str, Any]]:
        with self._fetch() as fetch:
            return research.get_candidate(fetch, self._strategy(fetch, key, version), observation_id)

    def research_snapshot(self, key: str, version: str, snapshot_id: int) -> Optional[Dict[str, Any]]:
        with self._fetch() as fetch:
            return research.get_snapshot(fetch, self._strategy(fetch, key, version), snapshot_id)

    def overall_performance(self, strategy_id: Optional[int] = None) -> Dict[str, Any]:
        with self._fetch() as fetch:
            return analytics.overall_performance(fetch, strategy_id)
