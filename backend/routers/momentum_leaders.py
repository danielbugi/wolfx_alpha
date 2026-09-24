# File: backend/routers/momentum_leaders.py
"""Momentum Leaders API router -- see services/momentum_leaders_service.py."""

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import require_authenticated_user
import logging

from services.momentum_leaders_service import MomentumLeadersService

logger = logging.getLogger(__name__)

momentum_leaders_router = APIRouter(
    prefix="/api/momentum-leaders",
    tags=["momentum-leaders"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)


def get_momentum_leaders_service():
    from main import get_database_connection
    return MomentumLeadersService(get_database_connection)


@momentum_leaders_router.get("")
def get_momentum_leaders(service: MomentumLeadersService = Depends(get_momentum_leaders_service)):
    """
    Of the stocks that made a Momentum Board list on one of the previous few sessions, who moved
    most today (podium + a few more ranks). None of this is a track record -- see board.py's
    docstring: consecutive days of the same stock overlap and earlier lists say nothing about
    later ones.
    """
    try:
        return service.get_leaders()
    except Exception as e:
        logger.error(f"Error getting momentum leaders: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving momentum leaders: {str(e)}")
