# File: backend/routers/market.py
"""
Real market-index/macro data API router — distinct from /api/screener's
"market overview" (which is breadth stats over the internal stock universe).
"""

from fastapi import APIRouter, HTTPException, Query, Depends

from auth.dependencies import require_authenticated_user
import logging

from services.market_data_service import MarketDataService

logger = logging.getLogger(__name__)

market_router = APIRouter(
    prefix="/api/market",
    tags=["market"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)


def get_market_data_service():
    from main import get_database_connection
    return MarketDataService(get_database_connection)


@market_router.get("/indices")
def get_market_indices(
        sparkline_days: int = Query(30, ge=1, le=365, description="Number of trailing days in each sparkline series"),
        market_data_service: MarketDataService = Depends(get_market_data_service),
):
    """
    Latest close/$change/%change plus a trailing sparkline for the fixed
    index/commodity/macro symbol list (S&P 500, Nasdaq, Russell 2000, Dow,
    VIX, 10Y yield, Gold, Crude, DXY, BTC). VIX also carries a `regime`
    field (calm/normal/elevated/fear).
    """
    try:
        return market_data_service.get_market_indices(sparkline_days=sparkline_days)
    except Exception as e:
        logger.error(f"Error getting market indices: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting market indices: {str(e)}")


@market_router.get("/sectors/history")
def get_sector_history(
        days: int = Query(90, ge=1, le=365, description="Number of trailing days of sector performance history"),
        market_data_service: MarketDataService = Depends(get_market_data_service),
):
    """
    Daily sector-performance history (avg % change, stock count) for the
    dashboard's sector Heatmap/Trend toggle.
    """
    try:
        return market_data_service.get_sector_history(days=days)
    except Exception as e:
        logger.error(f"Error getting sector history: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting sector history: {str(e)}")
