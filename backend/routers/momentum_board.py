# File: backend/routers/momentum_board.py
"""
Momentum Board API router — replaces the old "Alpha Finder" as the dashboard's main
find-big-winners panel. See services/momentum_board_service.py for the methodology.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import require_authenticated_user
from typing import Optional
import logging

from services.momentum_board_service import MomentumBoardService

logger = logging.getLogger(__name__)

momentum_board_router = APIRouter(
    prefix="/api/momentum-board",
    tags=["momentum-board"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)


def get_momentum_board_service():
    from main import get_database_connection
    return MomentumBoardService(get_database_connection)


@momentum_board_router.get("")
def get_momentum_board(
        category: Optional[str] = Query(None, description="breakout | near_breakout | all"),
        sector: Optional[str] = Query(None),
        min_quality_grade: Optional[str] = Query(None, description="A | B | C | D"),
        limit: int = Query(100, ge=1, le=500),
        service: MomentumBoardService = Depends(get_momentum_board_service),
):
    """
    Today's (most recent session's) breakout / near-breakout stocks that made the top ranks
    of 2+ of the day's fact lists (gainers, ATR expansion, volume surge) -- the same
    confluence rule validated for the Telegram channel, read from its stored snapshot
    (digest_runs / digest_stocks). Confirmed (starred) names sort first, then the biggest
    1-day movers.
    """
    try:
        return service.get_board(
            category=category,
            sector=sector,
            min_quality_grade=min_quality_grade,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"Error getting momentum board: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving momentum board: {str(e)}")
