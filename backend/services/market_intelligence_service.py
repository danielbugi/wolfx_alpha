# File: backend/services/market_intelligence_service.py
"""
Market Intelligence - the backend side. Read-only: binds mechanism/market_intelligence (store + payload) to the backend's connection
pool. Observed rows only unless the caller explicitly opts in to reconstructed ones; every response carries its provenance.

If migrations 24/25 have not been applied (or the runtime role cannot read the tables yet) every endpoint answers with an explicit
`available: false` payload -- never an empty success that could be mistaken for "no data today", and never a 500.
"""
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

import psycopg2.errors

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from market_intelligence import payload as mi_payload  # noqa: E402
from market_intelligence import store as mi_store  # noqa: E402

NOT_PROVISIONED = "not_provisioned"


class MarketIntelligenceService:
    def __init__(self, get_connection: Callable):
        self._get_connection = get_connection

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        conn = self._get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    @staticmethod
    def _unprovisioned() -> Dict[str, Any]:
        return mi_payload.unavailable(NOT_PROVISIONED, "Market Intelligence storage is not provisioned on this database yet.")

    def definitions(self) -> Dict[str, Any]:
        return mi_payload.definitions()

    def snapshot(self, session: Optional[date], include_reconstructed: bool) -> Dict[str, Any]:
        try:
            with self._cursor() as cur:
                market = mi_store.get_market_snapshot(cur, session, None, include_reconstructed)
                sectors = mi_store.get_sector_snapshots(cur, date.fromisoformat(str(market["session_date"])), None,
                                                        include_reconstructed) if market else []
                return mi_payload.build(market, sectors)
        except (psycopg2.errors.UndefinedTable, psycopg2.errors.InsufficientPrivilege):
            return self._unprovisioned()

    def history(self, limit: int, include_reconstructed: bool) -> Dict[str, Any]:
        try:
            with self._cursor() as cur:
                return {"schema": mi_payload.SCHEMA, "available": True, "include_reconstructed": include_reconstructed,
                        "sessions": mi_store.list_market_snapshots(cur, limit, None, include_reconstructed)}
        except (psycopg2.errors.UndefinedTable, psycopg2.errors.InsufficientPrivilege):
            return self._unprovisioned()
