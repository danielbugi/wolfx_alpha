# File: backend/routers/earnings.py
"""Batch earnings dates (GET /api/earnings?symbol=A&symbol=B...): the Earnings page's watchlist rows and the Overview
earnings card in ONE request and ONE query, from the same read model as /api/stock/{symbol}/earnings."""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import require_authenticated_user
from services.earnings_read_model import MAX_SYMBOLS, EarningsReadModel, RULE, SOURCE, ny_today

logger = logging.getLogger(__name__)

earnings_router = APIRouter(prefix="/api/earnings", tags=["earnings"], dependencies=[Depends(require_authenticated_user)])


def get_earnings_read_model():
    from main import get_database_connection
    return EarningsReadModel(get_database_connection)


@earnings_router.get("")
def earnings_batch(symbol: List[str] = Query(...), past: int = Query(12, ge=0, le=40),
                   model: EarningsReadModel = Depends(get_earnings_read_model)):
    symbols = [s for s in symbol if s.strip()]
    if not symbols or len(set(s.upper() for s in symbols)) > MAX_SYMBOLS:
        raise HTTPException(status_code=422, detail=f"between 1 and {MAX_SYMBOLS} symbols")
    if any(len(s) > 10 or not all(c.isalnum() or c in ".-" for c in s) for s in symbols):
        raise HTTPException(status_code=422, detail="symbols must be letters, digits, '.' or '-'")
    try:
        data = model.for_symbols(symbols, past_limit=past)
    except Exception as e:
        logger.error(f"Earnings batch read failed: {e}")
        raise HTTPException(status_code=503, detail="Earnings dates are unavailable right now")
    return {"source": SOURCE, "rule": RULE, "today": ny_today().isoformat(), "symbols": data}
