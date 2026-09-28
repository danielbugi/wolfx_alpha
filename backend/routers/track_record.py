# File: backend/routers/track_record.py
"""
Track Record API Router - the live outcome ledger's authenticated summary and signal list.
See services/track_record_service.py for the honesty rules (min sample size, closed/open kept
separate) and mechanism/add_signal_ledger_tables.sql for the underlying table.

Authenticated only for now, like every other router in this backend -- an ungated public summary
is a deliberate, separate follow-up once the numbers and copy have been validated internally, not
this pass. Named track_record, not performance -- routers/performance.py already means API-route
latency.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from auth.dependencies import require_authenticated_user
import logging

from services.track_record_service import TrackRecordService

logger = logging.getLogger(__name__)

track_record_router = APIRouter(
    prefix="/api/track-record",
    tags=["track-record"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)


def get_track_record_service():
    from main import get_database_connection
    return TrackRecordService(get_database_connection)


@track_record_router.get("/summary")
def summary(service: TrackRecordService = Depends(get_track_record_service)):
    return service.get_summary()


@track_record_router.get("/signals")
def recent_signals(
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None, description="Filter to one status, e.g. 'open' or 'stopped'"),
    service: TrackRecordService = Depends(get_track_record_service),
):
    return service.get_recent_signals(limit=limit, status=status)


@track_record_router.get("/signals/{symbol}")
def signals_for_symbol(symbol: str, service: TrackRecordService = Depends(get_track_record_service)):
    return service.get_by_symbol(symbol.upper())
