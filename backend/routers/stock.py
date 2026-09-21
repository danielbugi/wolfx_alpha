# File: backend/routers/stock.py
"""
Stock Detail API Router - Comprehensive per-symbol data
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from services.stock_service import StockService
from services.strategy_calc import compute_strategy_plan
from services.earnings_service import get_earnings_dates
from utils import load_ml_enhanced_data_cached

logger = logging.getLogger(__name__)

stock_router = APIRouter(
    prefix="/api/stock",
    tags=["stock"],
    responses={404: {"description": "Not found"}},
)

SIGNAL_BUCKETS = ["bullish_breakout", "bearish_breakout", "near_bullish", "near_bearish"]


def get_stock_service():
    from main import get_database_connection
    return StockService(get_database_connection)


def _find_active_signal(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Look up a symbol in the latest screener output. Prefers
    ai_insights.ml_enhanced_signals since those entries carry the ML rating fields
    (ml_confidence, ml_momentum_probability, summary_text, ...) on top of the base
    technicals/fundamentals; falls back to the plain signals buckets for symbols that
    have a signal today but weren't ML-enhanced (no model prediction available).
    """
    ml_data = load_ml_enhanced_data_cached()
    if not ml_data:
        return None

    for entry in ml_data.get("ai_insights", {}).get("ml_enhanced_signals", []):
        if entry.get("symbol") == symbol:
            return entry

    signals = ml_data.get("signals", {})
    for bucket in SIGNAL_BUCKETS:
        for entry in signals.get(bucket, []):
            if entry.get("symbol") == symbol:
                return entry
    return None


@stock_router.get("/search")
def search_symbols(
        q: str = Query(..., min_length=1),
        limit: int = Query(10, ge=1, le=25),
        stock_service: StockService = Depends(get_stock_service)
):
    """Symbol-prefix search for the nav search box"""
    try:
        return stock_service.search_symbols(q, limit)
    except Exception as e:
        logger.error(f"Error searching symbols: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching symbols: {str(e)}")


@stock_router.get("/{symbol}/price-history")
def get_price_history(
        symbol: str,
        days: int = Query(180, ge=5, le=730),
        stock_service: StockService = Depends(get_stock_service)
):
    """OHLCV history with the Donchian channel, for the price chart"""
    symbol = symbol.upper()
    try:
        history = stock_service.get_price_history(symbol, days)
        if not history:
            raise HTTPException(status_code=404, detail=f"No price history found for '{symbol}'")
        return {"symbol": symbol, "days": days, "bars": history}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting price history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving price history: {str(e)}")


@stock_router.get("/{symbol}/signal")
def get_signal_summary(symbol: str):
    """AI signal chip only, no DB access -- for the dashboard hover card, which
    already has price/Donchian data from price-history and just needs this."""
    symbol = symbol.upper()
    active_signal = _find_active_signal(symbol)
    if not active_signal:
        return {"symbol": symbol, "has_signal": False}
    return {
        "symbol": symbol,
        "has_signal": True,
        "signal_type": active_signal.get("signal_type"),
        "ml_confidence": active_signal.get("ml_confidence"),
        "urgency": active_signal.get("urgency"),
        "alignment_grade": active_signal.get("alignment_grade"),
    }


@stock_router.get("/{symbol}/earnings")
def get_earnings(symbol: str, limit: int = Query(12, ge=1, le=40)):
    """Earnings-date markers for the chart, fetched from yfinance on demand
    (see services/earnings_service.py for why -- no other provider covers this
    for the full universe). Declared as a plain `def`, not `async def` like the
    rest of this router: yfinance does blocking HTTP that can take several
    seconds or hang under rate limiting, and awaiting it directly would stall
    the whole event loop. A sync route runs in Starlette's threadpool instead."""
    symbol = symbol.upper()
    try:
        dates = get_earnings_dates(symbol, limit)
    except Exception as e:
        logger.warning(f"Earnings lookup failed for {symbol}: {e}")
        dates = []
    return {"symbol": symbol, "earnings_dates": dates}


@stock_router.get("/{symbol}")
def get_stock_detail(
        symbol: str,
        stock_service: StockService = Depends(get_stock_service)
):
    """
    Comprehensive single-stock detail: technicals (daily/weekly/monthly),
    fundamentals + quality score, AI rating/confidence, quarterly financials,
    and historical breakout track record.
    """
    symbol = symbol.upper()

    try:
        active_signal = _find_active_signal(symbol)

        if active_signal:
            data_source = "active_signal"
            snapshot = active_signal
            plan = compute_strategy_plan(active_signal)
            ai_rating = {
                "signal_type": active_signal.get("signal_type"),
                "urgency": active_signal.get("urgency"),
                "ml_confidence": active_signal.get("ml_confidence"),
                "ml_momentum_probability": active_signal.get("ml_momentum_probability"),
                "alignment_score": active_signal.get("alignment_score"),
                "alignment_grade": active_signal.get("alignment_grade"),
                "signal_strength": active_signal.get("signal_strength"),
                "reasoning": active_signal.get("summary_text"),
                "ml_trade_recommendation": active_signal.get("ml_trade_recommendation"),
                "plan": plan,
            }
        else:
            if not stock_service.symbol_exists(symbol):
                raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")

            data_source = "no_signal"
            technicals = stock_service.get_technical_snapshot(symbol)
            fundamentals = stock_service.get_fundamentals_snapshot(symbol)
            snapshot = {**technicals, **fundamentals}
            ai_rating = {
                "signal_type": "no_signal",
                "urgency": None,
                "ml_confidence": None,
                "ml_momentum_probability": None,
                "alignment_score": None,
                "alignment_grade": None,
                "signal_strength": None,
                "reasoning": "No active breakout signal today — this symbol did not "
                             "appear in the latest screening run.",
                "ml_trade_recommendation": None,
                "plan": None,
            }

        quarterly_financials = stock_service.get_quarterly_financials(symbol)
        breakout_track_record = stock_service.get_breakout_history(symbol)

        return {
            "symbol": symbol,
            "data_source": data_source,
            "snapshot": snapshot,
            "ai_rating": ai_rating,
            "quarterly_financials": quarterly_financials,
            "breakout_track_record": breakout_track_record,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stock detail for {symbol}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error retrieving stock detail: {str(e)}")
