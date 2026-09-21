# File: backend/models/screener_models.py
"""
Screener Data Models - Request/Response structures for screener API
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum


class QualityGrade(str, Enum):
    """Stock quality grades"""
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class TechnicalStrength(str, Enum):
    """Technical strength ratings"""
    STRONG = "Strong"
    MODERATE = "Moderate"
    NEUTRAL = "Neutral"
    WEAK = "Weak"
    UNKNOWN = "Unknown"


class SortBy(str, Enum):
    """Sorting options for screener results"""
    PRICE_CHANGE = "price_change_pct"
    VOLUME = "volume"
    VOLUME_RATIO = "volume_ratio"
    RSI = "rsi_14"
    MARKET_CAP = "market_cap"
    QUALITY_SCORE = "quality_score"
    TECHNICAL_STRENGTH = "technical_strength"


class SortOrder(str, Enum):
    """Sort order options"""
    ASC = "asc"
    DESC = "desc"


# ============================================================================
# REQUEST MODELS
# ============================================================================

class ScreenerFilterRequest(BaseModel):
    """Request model for stock screening with filters"""

    # Price filters
    min_price: Optional[float] = Field(None, ge=0, description="Minimum stock price")
    max_price: Optional[float] = Field(None, ge=0, description="Maximum stock price")

    # Volume filters
    min_volume: Optional[int] = Field(None, ge=0, description="Minimum daily volume")
    min_volume_ratio: Optional[float] = Field(None, ge=0, description="Minimum volume ratio (vs average)")

    # Market cap filters
    min_market_cap: Optional[int] = Field(None, ge=0, description="Minimum market capitalization")
    max_market_cap: Optional[int] = Field(None, ge=0, description="Maximum market capitalization")

    # Performance filters
    min_price_change: Optional[float] = Field(None, description="Minimum price change percentage")
    max_price_change: Optional[float] = Field(None, description="Maximum price change percentage")

    # Technical filters
    min_rsi: Optional[float] = Field(None, ge=0, le=100, description="Minimum RSI value")
    max_rsi: Optional[float] = Field(None, ge=0, le=100, description="Maximum RSI value")

    # Fundamental filters
    sectors: Optional[List[str]] = Field(None, description="List of sectors to include")
    quality_grades: Optional[List[QualityGrade]] = Field(None, description="Quality grades to include")
    min_quality_score: Optional[float] = Field(None, ge=0, le=100, description="Minimum quality score")

    # Result options
    limit: Optional[int] = Field(50, ge=1, le=500, description="Maximum number of results")
    sort_by: Optional[SortBy] = Field(SortBy.PRICE_CHANGE, description="Field to sort by")
    sort_order: Optional[SortOrder] = Field(SortOrder.DESC, description="Sort order")

    class Config:
        schema_extra = {
            "example": {
                "min_price": 10.0,
                "max_price": 500.0,
                "min_volume": 100000,
                "min_volume_ratio": 1.5,
                "min_market_cap": 1000000000,
                "sectors": ["Technology", "Healthcare"],
                "quality_grades": ["A", "B"],
                "min_rsi": 30,
                "max_rsi": 70,
                "limit": 20,
                "sort_by": "price_change_pct",
                "sort_order": "desc"
            }
        }


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class StockResult(BaseModel):
    """Individual stock result from screener"""
    symbol: str
    current_price: float
    price_change_pct: float
    volume: int
    volume_ratio: float
    rsi_14: Optional[float]
    sector: str
    market_cap: Optional[int]
    pe_ratio: Optional[float]
    quality_grade: Optional[QualityGrade]
    quality_score: Optional[float]
    donchian_position: float
    technical_strength: TechnicalStrength


class ScreenerResponse(BaseModel):
    """Response model for screener results"""
    filters_applied: Dict[str, Any]
    total_matches: int
    results: List[StockResult]
    facets: Dict[str, Any]
    execution_time_ms: float

    class Config:
        schema_extra = {
            "example": {
                "filters_applied": {
                    "min_price": 10.0,
                    "sectors": ["Technology"],
                    "min_volume_ratio": 1.5
                },
                "total_matches": 47,
                "results": [
                    {
                        "symbol": "AAPL",
                        "current_price": 234.35,
                        "price_change_pct": 2.15,
                        "volume": 45123000,
                        "volume_ratio": 1.8,
                        "rsi_14": 65.2,
                        "sector": "Technology",
                        "market_cap": 3600000000000,
                        "pe_ratio": 28.5,
                        "quality_grade": "A",
                        "quality_score": 87.3,
                        "donchian_position": 78.5,
                        "technical_strength": "Strong"
                    }
                ],
                "facets": {
                    "sectors": {"Technology": 15, "Healthcare": 8},
                    "quality_grades": {"A": 12, "B": 20}
                },
                "execution_time_ms": 145.2
            }
        }


class MarketOverviewResponse(BaseModel):
    """Response model for market overview"""
    market_breadth: Dict[str, Any]
    market_indicators: Dict[str, Any]
    sector_performance: List[Dict[str, Any]]

    class Config:
        schema_extra = {
            "example": {
                "market_breadth": {
                    "total_stocks": 1005,
                    "gainers": 567,
                    "losers": 438,
                    "advance_decline_ratio": 1.29,
                    "unusual_volume_count": 89,
                    "overbought_count": 45,
                    "oversold_count": 23
                },
                "market_indicators": {
                    "avg_volume_ratio": 1.23,
                    "avg_rsi": 52.8,
                    "total_market_cap": 45000000000000
                },
                "sector_performance": [
                    {
                        "sector": "Technology",
                        "stock_count": 145,
                        "avg_performance": 1.85
                    }
                ]
            }
        }


class FilterOptionsResponse(BaseModel):
    """Response model for available filter options"""
    sectors: List[Dict[str, Any]]
    price_ranges: Dict[str, Any]
    market_cap_ranges: Dict[str, Any]
    quality_grades: List[str]
    technical_ranges: Dict[str, Any]

    class Config:
        schema_extra = {
            "example": {
                "sectors": [
                    {"name": "Technology", "stock_count": 145},
                    {"name": "Healthcare", "stock_count": 87}
                ],
                "price_ranges": {
                    "min": 1.05,
                    "max": 1245.80,
                    "percentiles": {
                        "25th": 25.40,
                        "median": 67.80,
                        "75th": 189.50
                    }
                },
                "market_cap_ranges": {
                    "min": 50000000,
                    "max": 3600000000000,
                    "percentiles": {
                        "25th": 500000000,
                        "median": 2500000000,
                        "75th": 15000000000
                    }
                },
                "quality_grades": ["A", "B", "C", "D", "F"],
                "technical_ranges": {
                    "rsi": {"min": 0, "max": 100, "oversold": 30, "overbought": 70},
                    "volume_ratio": {"min": 0.1, "max": 10.0, "normal": 1.0, "unusual": 2.0}
                }
            }
        }


# ============================================================================
# PRESET MODELS
# ============================================================================

class FilterPreset(BaseModel):
    """Model for saved filter presets"""
    name: str = Field(..., description="Name of the preset")
    description: Optional[str] = Field(None, description="Description of the preset")
    filters: ScreenerFilterRequest = Field(..., description="Filter configuration")

    class Config:
        schema_extra = {
            "example": {
                "name": "High Growth Tech",
                "description": "Technology stocks with high growth potential",
                "filters": {
                    "sectors": ["Technology"],
                    "min_price_change": 2.0,
                    "min_volume_ratio": 1.5,
                    "quality_grades": ["A", "B"],
                    "min_market_cap": 1000000000,
                    "sort_by": "price_change_pct",
                    "limit": 25
                }
            }
        }


class PresetResponse(BaseModel):
    """Response model for preset operations"""
    presets: List[FilterPreset]


# ============================================================================
# COMMON PRESETS (Built-in)
# ============================================================================

COMMON_PRESETS = [
    FilterPreset(
        name="Growth Stocks",
        description="High-growth stocks with strong momentum",
        filters=ScreenerFilterRequest(
            min_price_change=3.0,
            min_volume_ratio=1.5,
            quality_grades=[QualityGrade.A, QualityGrade.B],
            min_market_cap=500000000,
            sort_by=SortBy.PRICE_CHANGE,
            limit=25
        )
    ),
    FilterPreset(
        name="Value Plays",
        description="Undervalued stocks with quality fundamentals",
        filters=ScreenerFilterRequest(
            max_price_change=1.0,
            min_quality_score=70.0,
            quality_grades=[QualityGrade.A, QualityGrade.B],
            min_market_cap=1000000000,
            sort_by=SortBy.QUALITY_SCORE,
            limit=25
        )
    ),
    FilterPreset(
        name="Breakout Candidates",
        description="Stocks near technical breakouts with volume",
        filters=ScreenerFilterRequest(
            min_volume_ratio=2.0,
            min_rsi=50.0,
            max_rsi=75.0,
            min_price=5.0,
            sort_by=SortBy.VOLUME_RATIO,
            limit=30
        )
    ),
    FilterPreset(
        name="Large Cap Leaders",
        description="Large cap stocks with strong performance",
        filters=ScreenerFilterRequest(
            min_market_cap=10000000000,
            quality_grades=[QualityGrade.A],
            min_price_change=0.5,
            sort_by=SortBy.MARKET_CAP,
            limit=20
        )
    ),
    FilterPreset(
        name="Oversold Recovery",
        description="Quality stocks in oversold territory",
        filters=ScreenerFilterRequest(
            max_rsi=35.0,
            quality_grades=[QualityGrade.A, QualityGrade.B],
            min_market_cap=500000000,
            sort_by=SortBy.RSI,
            sort_order=SortOrder.ASC,
            limit=25
        )
    )
]