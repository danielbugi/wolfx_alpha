# File: backend/routers/forward_tracker.py
"""
Forward paper tracker dashboard API - internal, authenticated, READ-ONLY. One GET returning the aggregated summary of the stored forward paper observations (positions by variant, realised net R at
0 / 10 / 25 bps, MFE / MAE, marks of open positions, hindsight big-winner opportunity, data health). Paper observation only: no order exists, nothing here influences screening, signals or the
tracker. Historical replay results are not part of this payload.
"""
from fastapi import APIRouter, Depends

from auth.dependencies import require_authenticated_user
from services.forward_tracker_service import ForwardTrackerService

forward_tracker_router = APIRouter(
    prefix="/api/lab/forward-tracker",
    tags=["lab"],
    dependencies=[Depends(require_authenticated_user)],
)

_SERVICE = None


def get_forward_tracker_service() -> ForwardTrackerService:
    """One service per process so its short cache is shared between requests."""
    global _SERVICE
    if _SERVICE is None:
        from main import get_database_connection
        _SERVICE = ForwardTrackerService(get_database_connection)
    return _SERVICE


@forward_tracker_router.get("")
def summary(service: ForwardTrackerService = Depends(get_forward_tracker_service)):
    return service.summary()
