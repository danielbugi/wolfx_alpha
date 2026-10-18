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

# Feature sets the API serves, newest definition first. The collector's runner module (which the backend must
# not import -- see test_activation_neutral) writes "mi_v2"; store.py's own default is still "mi_v1". Reading only the
# store default returned "no snapshot" while observed mi_v2 snapshots were in the table. The newest session across these
# wins; on the same session an observed row beats a reconstructed one, then the newer feature set. Every payload carries
# its feature_set_version in `meta`. mechanism/market_intelligence/tests pins that the first entry is the runner's.
SERVED_FEATURE_SETS = ("mi_v2", "mi_v1")

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
                candidates = []
                for rank, fsv in enumerate(SERVED_FEATURE_SETS):
                    row = mi_store.get_market_snapshot(cur, session, None, include_reconstructed, fsv)
                    if row:
                        candidates.append((str(row["session_date"]), row.get("provenance") == "observed", -rank, fsv, row))
                best = max(candidates, key=lambda c: c[:3]) if candidates else None
                market, fsv = (best[4], best[3]) if best else (None, None)
                sectors = mi_store.get_sector_snapshots(cur, date.fromisoformat(str(market["session_date"])), None,
                                                        include_reconstructed, fsv) if market else []
                return mi_payload.build(market, sectors)
        except (psycopg2.errors.UndefinedTable, psycopg2.errors.InsufficientPrivilege):
            return self._unprovisioned()

    def history(self, limit: int, include_reconstructed: bool) -> Dict[str, Any]:
        try:
            with self._cursor() as cur:
                merged: Dict[Any, Dict[str, Any]] = {}
                for fsv in SERVED_FEATURE_SETS:                       # newer feature set first: it wins a (session, provenance) tie
                    for row in mi_store.list_market_snapshots(cur, limit, None, include_reconstructed, fsv):
                        merged.setdefault((str(row["session_date"]), row["provenance"]), row)
                sessions = sorted(merged.values(), key=lambda r: (str(r["session_date"]), r["provenance"] == "observed"), reverse=True)
                return {"schema": mi_payload.SCHEMA, "available": True, "include_reconstructed": include_reconstructed,
                        "sessions": sessions[: max(1, min(int(limit), 500))]}
        except (psycopg2.errors.UndefinedTable, psycopg2.errors.InsufficientPrivilege):
            return self._unprovisioned()
