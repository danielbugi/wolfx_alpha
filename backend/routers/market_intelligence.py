# File: backend/routers/market_intelligence.py
"""
Market Intelligence API - internal, authenticated, READ-ONLY. The session-level market regime (risk_regime_v1, a V1 heuristic) and the
sector / relative-strength snapshot (rs_v1), both strategy-neutral. Nothing here influences screening, scoring or Donchian eligibility.

Observed rows only by default. `include_reconstructed=true` is the explicit opt-in to rows recomputed after the fact; the response always
states each row's provenance.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from auth.dependencies import require_authenticated_user
from services.market_intelligence_service import MarketIntelligenceService

market_intelligence_router = APIRouter(
    prefix="/api/market-intelligence",
    tags=["market-intelligence"],
    dependencies=[Depends(require_authenticated_user)],
)


def get_market_intelligence_service() -> MarketIntelligenceService:
    from main import get_database_connection
    return MarketIntelligenceService(get_database_connection)


@market_intelligence_router.get("/definitions")
def definitions(service: MarketIntelligenceService = Depends(get_market_intelligence_service)):
    return service.definitions()


@market_intelligence_router.get("/snapshot")
def snapshot(session: Optional[date] = None, include_reconstructed: bool = False,
             service: MarketIntelligenceService = Depends(get_market_intelligence_service)):
    return service.snapshot(session, include_reconstructed)


@market_intelligence_router.get("/history")
def history(limit: int = Query(30, ge=1, le=250), include_reconstructed: bool = False,
            service: MarketIntelligenceService = Depends(get_market_intelligence_service)):
    return service.history(limit, include_reconstructed)
