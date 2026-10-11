# File: backend/routers/news.py
"""
Stock news API - authenticated, read-only from the caller's side. `GET /api/news/{symbol}` returns recent headlines
(headline, source, time, link) through the shared, cached news service (services/news_service.py). The only write is
the service's own cache refresh, at most once per symbol per few hours.
"""
from fastapi import APIRouter, Depends, Path, Query

from auth.dependencies import require_authenticated_user
from services.news_service import MAX_LIMIT, BackendNewsStore, StockNewsService

news_router = APIRouter(
    prefix="/api/news",
    tags=["news"],
    dependencies=[Depends(require_authenticated_user)],
)

SYMBOL = Path(..., pattern=r"^[A-Za-z0-9.\-]{1,10}$")


def get_stock_news_service() -> StockNewsService:
    from main import get_database_connection
    return StockNewsService(BackendNewsStore(get_database_connection))


@news_router.get("/{symbol}")
def get_stock_news(
    symbol: str = SYMBOL,
    limit: int = Query(8, ge=1, le=MAX_LIMIT),
    service: StockNewsService = Depends(get_stock_news_service),
):
    return service.get(symbol.upper(), limit)
