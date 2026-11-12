"""Opportunity Intelligence v1 for the owner dashboard: a thin read-only wrapper around mechanism/opportunity (the facts are computed there,
never here). One pooled connection per request, READ ONLY session (mechanism/opportunity/repository.read_only)."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from opportunity import brief as B  # noqa: E402
from opportunity import evidence as E  # noqa: E402
from opportunity import repository as R  # noqa: E402


class OpportunityUnavailable(RuntimeError):
    pass


class OpportunityService:
    def __init__(self, get_connection: Callable[[], Any]):
        self._get_connection = get_connection

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        conn = self._get_connection()
        if conn is None:
            raise OpportunityUnavailable("database unavailable")
        try:
            yield conn
        finally:
            conn.close()

    def report(self, session: Optional[date], days: int, now: Optional[datetime] = None) -> Dict[str, Any]:
        if session is None:
            with self._connect() as conn:
                session = R.latest_session(conn)
        rep = B.run(self._connect, session, days)
        rep["provisional"] = B.cutoff(session) > (now or datetime.now(timezone.utc))   # the knowledge cutoff has not passed yet
        return rep

    VIEWS = ("all", "agreeing", "discovery")

    def briefs(self, session: Optional[date] = None, days: int = B.DEFAULT_WINDOW_DAYS, view: str = "all", limit: int = 100) -> Dict[str, Any]:
        """view: all | agreeing (business improved and the market response was positive, nothing disagreeing) | discovery (business improved,
        market response flat or negative). Categories describe evidence; none is a forecast or a recommendation."""
        if view not in self.VIEWS:
            raise ValueError(f"view must be one of {self.VIEWS}")
        rep = self.report(session, days)
        items = rep["briefs"]
        if view == "agreeing":
            items = [b for b in items if b["assessment"]["converging"]]
        elif view == "discovery":
            items = [b for b in items if b["assessment"]["category"] == E.CATEGORY_DISCOVERY]
        return {**{k: v for k, v in rep.items() if k != "briefs"}, "view": view, "matching": len(items), "returned": min(limit, len(items)), "briefs": items[:limit]}

    def company(self, symbol: str, session: Optional[date] = None, days: int = B.DEFAULT_WINDOW_DAYS) -> Optional[Dict[str, Any]]:
        rep = self.report(session, days)
        hits = [b for b in rep["briefs"] if b["symbol"] == symbol.upper()]
        if not hits:
            return None
        return {**{k: v for k, v in rep.items() if k != "briefs"}, "briefs": hits}
