# File: backend/routers/screener.py
"""
Screener API Router - Advanced stock screening endpoints
"""

from fastapi import APIRouter, HTTPException, Depends

from auth.dependencies import require_authenticated_user
from typing import List, Dict, Any
import time
import logging
from datetime import datetime

from models.screener_models import (
    ScreenerFilterRequest, ScreenerResponse, StockResult,
    MarketOverviewResponse, FilterOptionsResponse,
    FilterPreset, PresetResponse, COMMON_PRESETS,
    QualityGrade, TechnicalStrength, SortBy, SortOrder
)
from services.market_service import MarketService, MarketFilter

logger = logging.getLogger(__name__)

# Create router
screener_router = APIRouter(
    prefix="/api/screener",
    tags=["screener"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)


# Dependency to get market service
def get_market_service():
    """Dependency to create market service instance"""
    # Import here to avoid circular imports
    from main import get_database_connection
    return MarketService(get_database_connection)


# ============================================================================
# SCREENER ENDPOINTS
# ============================================================================

@screener_router.post("/search", response_model=ScreenerResponse)
def search_stocks(
        filter_request: ScreenerFilterRequest,
        market_service: MarketService = Depends(get_market_service)
):
    """
    Search stocks with advanced filtering criteria

    Apply sophisticated filters to find stocks matching your criteria:
    - Price and volume filters
    - Technical indicators (RSI, volume ratio)
    - Fundamental criteria (sector, quality grade, market cap)
    - Performance filters (price change)
    """
    start_time = time.time()

    try:
        # Convert request to MarketFilter
        market_filter = MarketFilter(
            min_price=filter_request.min_price,
            max_price=filter_request.max_price,
            min_volume=filter_request.min_volume,
            min_market_cap=filter_request.min_market_cap,
            max_market_cap=filter_request.max_market_cap,
            sectors=filter_request.sectors,
            min_price_change=filter_request.min_price_change,
            max_price_change=filter_request.max_price_change,
            min_volume_ratio=filter_request.min_volume_ratio,
            min_rsi=filter_request.min_rsi,
            max_rsi=filter_request.max_rsi,
            quality_grade=[grade.value for grade in
                           filter_request.quality_grades] if filter_request.quality_grades else None
        )

        # Get filtered results
        stocks = market_service.get_filtered_stocks(market_filter, filter_request.limit)

        # Apply additional filtering for quality score if specified
        if filter_request.min_quality_score:
            stocks = [
                stock for stock in stocks
                if stock.get('quality_score') and stock['quality_score'] >= filter_request.min_quality_score
            ]

        # Apply sorting
        if filter_request.sort_by and stocks:
            reverse = filter_request.sort_order == SortOrder.DESC
            sort_key = filter_request.sort_by.value

            # Handle special cases for sorting
            if sort_key in stocks[0]:
                stocks.sort(
                    key=lambda x: x.get(sort_key) or (0 if reverse else float('inf')),
                    reverse=reverse
                )

        # Convert to response models
        stock_results = []
        for stock in stocks:
            stock_results.append(StockResult(
                symbol=stock['symbol'],
                current_price=stock['current_price'],
                price_change_pct=stock['price_change_pct'],
                volume=stock['volume'],
                volume_ratio=stock['volume_ratio'],
                rsi_14=stock['rsi_14'],
                sector=stock['sector'],
                market_cap=stock['market_cap'],
                pe_ratio=stock['pe_ratio'],
                quality_grade=stock['quality_grade'],
                quality_score=stock['quality_score'],
                donchian_position=stock['donchian_position'],
                technical_strength=TechnicalStrength(stock['technical_strength'])
            ))

        # Generate facets for filtering UI
        facets = _generate_facets(stocks)

        # Build filters applied summary
        filters_applied = {}
        for field, value in filter_request.dict().items():
            if value is not None:
                filters_applied[field] = value

        execution_time = (time.time() - start_time) * 1000

        return ScreenerResponse(
            filters_applied=filters_applied,
            total_matches=len(stock_results),
            results=stock_results,
            facets=facets,
            execution_time_ms=round(execution_time, 2)
        )

    except Exception as e:
        logger.error(f"Error in stock search: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching stocks: {str(e)}")


@screener_router.get("/filters", response_model=FilterOptionsResponse)
def get_filter_options(market_service: MarketService = Depends(get_market_service)):
    """
    Get available filter options

    Returns all available filtering options including:
    - Available sectors and their stock counts
    - Price and market cap ranges with percentiles
    - Technical indicator ranges
    - Quality grades
    """
    try:
        filter_options = market_service.get_available_filters()

        return FilterOptionsResponse(**filter_options)

    except Exception as e:
        logger.error(f"Error getting filter options: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving filter options: {str(e)}")


@screener_router.get("/market-overview", response_model=MarketOverviewResponse)
def get_market_overview(market_service: MarketService = Depends(get_market_service)):
    """
    Get comprehensive market overview

    Provides market breadth analysis including:
    - Advance/decline statistics
    - Volume and momentum indicators
    - Sector performance breakdown
    - Overbought/oversold levels
    """
    try:
        overview = market_service.get_market_overview()

        return MarketOverviewResponse(**overview)

    except Exception as e:
        logger.error(f"Error getting market overview: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving market overview: {str(e)}")


# ============================================================================
# PRESET ENDPOINTS
# ============================================================================

@screener_router.get("/presets", response_model=PresetResponse)
async def get_filter_presets():
    """
    Get available filter presets

    Returns commonly used filter combinations:
    - Growth Stocks: High momentum with strong fundamentals
    - Value Plays: Undervalued quality stocks
    - Breakout Candidates: Technical setups with volume
    - Large Cap Leaders: Top performing large caps
    - Oversold Recovery: Quality stocks in oversold territory
    """
    return PresetResponse(presets=COMMON_PRESETS)


@screener_router.get("/presets/{preset_name}", response_model=ScreenerFilterRequest)
async def get_preset_by_name(preset_name: str):
    """Get specific filter preset by name"""
    for preset in COMMON_PRESETS:
        if preset.name.lower().replace(' ', '-') == preset_name.lower():
            return preset.filters

    raise HTTPException(status_code=404, detail=f"Preset '{preset_name}' not found")


@screener_router.post("/presets/{preset_name}/search", response_model=ScreenerResponse)
def search_with_preset(
        preset_name: str,
        market_service: MarketService = Depends(get_market_service)
):
    """
    Search stocks using a preset filter configuration

    Convenient endpoint to run searches with pre-configured filters.
    Available presets: growth-stocks, value-plays, breakout-candidates,
    large-cap-leaders, oversold-recovery
    """
    # Find the preset
    preset = None
    for p in COMMON_PRESETS:
        if p.name.lower().replace(' ', '-') == preset_name.lower():
            preset = p
            break

    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_name}' not found")

    # Use the main search endpoint with preset filters
    return search_stocks(preset.filters, market_service)


# ============================================================================
# ANALYTICS ENDPOINTS
# ============================================================================

@screener_router.get("/analytics/sector-breakdown")
def get_sector_breakdown(market_service: MarketService = Depends(get_market_service)):
    """Get detailed sector performance breakdown"""
    try:
        overview = market_service.get_market_overview()
        return {
            "sector_performance": overview["sector_performance"],
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting sector breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving sector breakdown: {str(e)}")


@screener_router.get("/analytics/technical-distribution")
def get_technical_distribution(market_service: MarketService = Depends(get_market_service)):
    """Get distribution of technical indicators across the market"""
    try:
        # This could be expanded to show RSI distribution, volume ratio distribution, etc.
        overview = market_service.get_market_overview()

        return {
            "technical_summary": {
                "overbought_stocks": overview["market_breadth"]["overbought_count"],
                "oversold_stocks": overview["market_breadth"]["oversold_count"],
                "unusual_volume_stocks": overview["market_breadth"]["unusual_volume_count"],
                "avg_rsi": overview["market_indicators"]["avg_rsi"],
                "avg_volume_ratio": overview["market_indicators"]["avg_volume_ratio"]
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting technical distribution: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving technical distribution: {str(e)}")


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _generate_facets(stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate facet counts for filtering UI"""
    facets = {
        "sectors": {},
        "quality_grades": {},
        "technical_strength": {},
        "market_cap_ranges": {
            "micro": 0,  # < 300M
            "small": 0,  # 300M - 2B
            "mid": 0,  # 2B - 10B
            "large": 0,  # 10B - 200B
            "mega": 0  # > 200B
        }
    }

    for stock in stocks:
        # Sector facets
        sector = stock.get('sector', 'Unknown')
        facets['sectors'][sector] = facets['sectors'].get(sector, 0) + 1

        # Quality grade facets
        grade = stock.get('quality_grade')
        if grade:
            facets['quality_grades'][grade] = facets['quality_grades'].get(grade, 0) + 1

        # Technical strength facets
        strength = stock.get('technical_strength', 'Unknown')
        facets['technical_strength'][strength] = facets['technical_strength'].get(strength, 0) + 1

        # Market cap range facets
        market_cap = stock.get('market_cap')
        if market_cap:
            if market_cap < 300_000_000:
                facets['market_cap_ranges']['micro'] += 1
            elif market_cap < 2_000_000_000:
                facets['market_cap_ranges']['small'] += 1
            elif market_cap < 10_000_000_000:
                facets['market_cap_ranges']['mid'] += 1
            elif market_cap < 200_000_000_000:
                facets['market_cap_ranges']['large'] += 1
            else:
                facets['market_cap_ranges']['mega'] += 1

    return facets