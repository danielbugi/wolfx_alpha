# File: backend/routers/market.py
"""
Real market-index/macro data API router — distinct from /api/screener's
"market overview" (which is breadth stats over the internal stock universe).
"""

from fastapi import APIRouter, HTTPException, Query, Depends

from auth.dependencies import require_authenticated_user
import logging

from services.market_data_service import MarketDataService
from services.market_calendar_service import get_market_calendar
from services.instruments import find as find_instrument

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


@market_router.get("/calendar")
def get_calendar():
    """
    US market sessions around today (New York dates): each trading day's close time, holidays and early closes
    included, from the pipeline's official calendar (mechanism/shared/market_calendar.py). `holidays_known` is false
    only on the calendar's weekday fallback, when holidays cannot be detected. Drives the sidebar's market clock.
    """
    try:
        return get_market_calendar()
    except Exception as e:
        logger.error(f"Error getting market calendar: {e}")
        raise HTTPException(status_code=500, detail="Error getting market calendar")


@market_router.get("/instruments")
def get_instruments(market_data_service: MarketDataService = Depends(get_market_data_service)):
    """
    Every instrument on the Markets pages (indices, index futures, metals, energy, agriculture, currencies, crypto,
    rates & volatility): last close, % change over calendar windows (1d/1w/1m/3m/ytd/1y, null when history doesn't
    reach) and a sparkline. An instrument without fetched history yet carries has_data: false.
    """
    try:
        return market_data_service.get_instruments()
    except Exception as e:
        logger.error(f"Error getting instruments: {e}")
        raise HTTPException(status_code=500, detail="Error getting instruments")


@market_router.get("/instruments/{symbol}/history")
def get_instrument_history(
        symbol: str,
        days: int = Query(730, ge=5, le=1825, description="Calendar days of daily history"),
        market_data_service: MarketDataService = Depends(get_market_data_service),
):
    """Daily OHLCV bars for one registered instrument, oldest first (same bar shape as a stock's price history)."""
    inst = find_instrument(symbol)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Unknown instrument '{symbol}'")
    try:
        bars = market_data_service.get_instrument_history(inst.symbol, days)
    except Exception as e:
        logger.error(f"Error getting instrument history: {e}")
        raise HTTPException(status_code=500, detail="Error getting instrument history")
    return {"symbol": inst.symbol, "name": inst.name, "group": inst.group, "unit": inst.unit, "bars": bars}
