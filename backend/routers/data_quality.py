# File: backend/routers/data_quality.py
"""
Quant Lab data-quality API - internal, authenticated, READ-ONLY. One GET: SEC ingestion coverage, SEC-vs-yfinance event reconciliation, and which event families are point-in-time (and so what
they may be used for). Nothing here writes, and nothing influences screening, signals, alerts or the research ledger.
"""
from fastapi import APIRouter, Depends

from auth.dependencies import require_authenticated_user
from services.data_quality_service import DataQualityService

data_quality_router = APIRouter(
    prefix="/api/lab/data-quality",
    tags=["lab"],
    dependencies=[Depends(require_authenticated_user)],
)

_SERVICE = None


def get_data_quality_service() -> DataQualityService:
    """One service per process so its cache is shared between requests."""
    global _SERVICE
    if _SERVICE is None:
        from main import get_database_connection
        _SERVICE = DataQualityService(get_database_connection)
    return _SERVICE


@data_quality_router.get("")
def summary(service: DataQualityService = Depends(get_data_quality_service)):
    return service.summary()
