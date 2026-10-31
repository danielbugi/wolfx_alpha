# File: backend/routers/inflection.py
"""
Fundamental Inflection API - authenticated, READ ONLY. Serves the events and features stored by mechanism/inflection (see services/inflection_service.py).
Research signals only; nothing here influences screening, scoring or any live signal.
"""
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import require_authenticated_user
from services.inflection_service import LABELS, InflectionService

logger = logging.getLogger(__name__)

inflection_router = APIRouter(
    prefix="/api/inflection",
    tags=["inflection"],
    dependencies=[Depends(require_authenticated_user)],
)


def get_inflection_service() -> InflectionService:
    from main import get_database_connection
    return InflectionService(get_database_connection)


def _table_missing(e: Exception) -> bool:
    return "does not exist" in str(e) and "fundamental_" in str(e) or "inflection_run_log" in str(e)


@inflection_router.get("/events")
def list_events(
    event_type: Optional[List[str]] = Query(None, description="one or more event types"),
    severity: Optional[str] = Query(None, pattern="^(low|medium|high)$"),
    sector: Optional[str] = None,
    min_strength: Optional[float] = Query(None, ge=0, le=100),
    min_market_cap: Optional[float] = Query(None, ge=0),
    max_market_cap: Optional[float] = Query(None, ge=0),
    since: Optional[date] = None,
    until: Optional[date] = None,
    q: Optional[str] = Query(None, max_length=10),
    sort: str = Query("strength", pattern="^(strength|available_at|score|symbol)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: InflectionService = Depends(get_inflection_service),
):
    bad = [t for t in (event_type or []) if t not in LABELS]
    if bad:
        raise HTTPException(status_code=422, detail={"code": "unknown_event_type", "types": bad})
    try:
        return service.list_events(event_types=event_type, severity=severity, sector=sector, min_strength=min_strength, min_market_cap=min_market_cap,
                                   max_market_cap=max_market_cap, since=since, until=until, q=q, sort=sort, limit=limit, offset=offset)
    except Exception as e:  # noqa: BLE001
        if _table_missing(e):
            return {"method_version": "inflection_v1", "available": False, "total": 0, "count": 0, "offset": offset, "events": [], "reason": "inflection tables are not installed yet"}
        logger.error(f"inflection events failed: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving inflection events")


@inflection_router.get("/summary")
def summary(days: int = Query(90, ge=1, le=730), service: InflectionService = Depends(get_inflection_service)):
    try:
        return service.summary(days=days)
    except Exception as e:  # noqa: BLE001
        if _table_missing(e):
            return {"method_version": "inflection_v1", "available": False, "features": 0, "events": 0, "by_type": [], "last_run": None, "reason": "inflection tables are not installed yet"}
        logger.error(f"inflection summary failed: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving inflection summary")


@inflection_router.get("/stock/{symbol}")
def stock(symbol: str, service: InflectionService = Depends(get_inflection_service)):
    sym = symbol.strip().upper()
    if not sym.replace("-", "").replace(".", "").isalnum() or len(sym) > 10:
        raise HTTPException(status_code=422, detail={"code": "invalid_symbol"})
    try:
        return service.stock(sym)
    except Exception as e:  # noqa: BLE001
        if _table_missing(e):
            return {"symbol": sym, "method_version": "inflection_v1", "available": False, "events": [], "trend": [], "latest_score": None, "reason": "inflection tables are not installed yet"}
        logger.error(f"inflection stock failed: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving inflection data")
