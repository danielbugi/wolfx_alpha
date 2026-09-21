# File: backend/models/stock_models.py
"""
Stock Detail Data Models - Response structures for the per-symbol detail API
"""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class PriceBar(BaseModel):
    """One OHLCV bar with the Donchian channel for that date"""
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: float
    volume: Optional[int] = None
    donchian_high_20: Optional[float] = None
    donchian_low_20: Optional[float] = None


class QuarterlyFinancials(BaseModel):
    """One quarter of fundamentals from quarterly_fundamentals"""
    quarter: Optional[str] = None
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[int] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    eps: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None
    eps_growth_yoy: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    free_cash_flow: Optional[float] = None


class BreakoutHistoryEntry(BaseModel):
    """One past breakout event for this symbol"""
    date: str
    breakout_type: Optional[str] = None
    entry_price: Optional[float] = None
    success: Optional[bool] = None
    max_gain_10d: Optional[float] = None
    max_loss_10d: Optional[float] = None
    days_to_peak: Optional[int] = None


class BreakoutTrackRecord(BaseModel):
    """Aggregate stats over a symbol's breakout history"""
    total_breakouts: int = 0
    win_rate: Optional[float] = None
    avg_gain_10d: Optional[float] = None
    avg_loss_10d: Optional[float] = None
    entries: List[BreakoutHistoryEntry] = []


class SymbolSearchResult(BaseModel):
    """One symbol match for the nav search box"""
    symbol: str
    sector: Optional[str] = None
    current_price: Optional[float] = None


class StockDetailResponse(BaseModel):
    """Comprehensive single-stock detail payload"""
    symbol: str
    data_source: str  # "active_signal" | "no_signal"

    # Core snapshot: merged technicals + fundamentals, either taken directly from
    # the latest screener signal (if the symbol has one today) or assembled from
    # the DB as a fallback. Kept as a flexible dict since the two sources have
    # slightly different shapes and both are already well-formed for display.
    snapshot: Dict[str, Any]

    # AI rating/confidence — always present, with signal_type "no_signal" when
    # the symbol has no active breakout today.
    ai_rating: Dict[str, Any]

    quarterly_financials: List[QuarterlyFinancials] = []
    breakout_track_record: BreakoutTrackRecord = BreakoutTrackRecord()
