"""Opportunity Intelligence v0 (owner-only, read-only): change briefs with their evidence. Descriptive: no score, no forecast, no advice.
docs/architecture/OPPORTUNITY_INTELLIGENCE.md."""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import require_owner
from services.opportunity_service import OpportunityService, OpportunityUnavailable

logger = logging.getLogger(__name__)

opportunity_router = APIRouter(
    prefix="/api/opportunity",
    tags=["opportunity"],
    dependencies=[Depends(require_owner)],
)


def get_opportunity_service() -> OpportunityService:
    from main import get_database_connection
    return OpportunityService(get_database_connection)


@opportunity_router.get("/briefs")
def list_briefs(session: Optional[date] = Query(None, description="Market session (default: newest session with an S&P 500 close)"),
                days: int = Query(14, ge=1, le=60), converging_only: bool = False, limit: int = Query(100, ge=1, le=1000),
                svc: OpportunityService = Depends(get_opportunity_service)):
    try:
        return svc.briefs(session, days, converging_only, limit)
    except (OpportunityUnavailable, LookupError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"opportunity briefs failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="opportunity briefs failed")


@opportunity_router.get("/briefs/{symbol}")
def company_brief(symbol: str, session: Optional[date] = None, days: int = Query(14, ge=1, le=60),
                  svc: OpportunityService = Depends(get_opportunity_service)):
    try:
        out = svc.company(symbol, session, days)
    except (OpportunityUnavailable, LookupError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    if out is None:
        raise HTTPException(status_code=404, detail="no change brief for this symbol in the window")
    return out
