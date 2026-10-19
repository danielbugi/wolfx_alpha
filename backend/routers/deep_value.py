# File: backend/routers/deep_value.py
"""
Deep Value / Earnings-Turnaround Alerts API Router

Internal research only (owner decision 2026-10-19: removed from the product). Owner-only on the server; the research
code and data are kept.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends

from auth.dependencies import require_owner
import logging

from services.deep_value_service import DeepValueService

logger = logging.getLogger(__name__)

deep_value_router = APIRouter(
    prefix="/api/deep-value",
    tags=["deep-value"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_owner)],
)


def get_deep_value_service():
    from main import get_database_connection
    return DeepValueService(get_database_connection)


@deep_value_router.get("/scan")
def scan_deep_value(
        near_low_pct: float = Query(15.0, ge=0, le=100, description="Max %% above the 12-month low to count as 'near lows'"),
        min_valuation_score: float = Query(12.0, ge=0, le=25, description="Min valuation sub-score (out of 25)"),
        min_financial_health_score: Optional[float] = Query(
            None, ge=0, le=25, deprecated=True,
            description="DEPRECATED (compatibility with the previous frontend): old market-cap + P/B floor. "
                        "Not applied when omitted; the balance-sheet checks always apply."),
        deep_value_service: DeepValueService = Depends(get_deep_value_service),
):
    """
    Scan for stocks near their 12-month low at a cheap valuation that do not
    fail a balance-sheet check (equity, leverage, liquidity, cash flow -- see
    deep_value_service.assess_balance_sheet). The subset that also just
    flipped from a quarterly net loss to a net profit is returned separately
    as turnaround_alerts (a historical field name kept for compatibility: a descriptive list, not a signal).
    """
    try:
        return deep_value_service.scan(
            near_low_pct=near_low_pct,
            min_valuation_score=min_valuation_score,
            min_financial_health_score=min_financial_health_score,
        )
    except Exception as e:
        logger.error(f"Error scanning for deep value: {e}")
        raise HTTPException(status_code=500, detail=f"Error scanning for deep value: {str(e)}")
