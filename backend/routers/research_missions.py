# File: backend/routers/research_missions.py
"""
Research Missions API - OWNER ONLY. Lists missions and their results, shows the research budget, and creates DRAFT missions.
Running a mission (and every paid model call) happens from the CLI by hand in the MVP; nothing here can start one.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser, require_owner
from services.research_missions_service import ResearchMissionsService

logger = logging.getLogger(__name__)

research_missions_router = APIRouter(prefix="/api/research", tags=["research-missions"], dependencies=[Depends(require_owner)])


def get_research_missions_service() -> ResearchMissionsService:
    from main import get_database_connection
    return ResearchMissionsService(get_database_connection)


def _unavailable(e: Exception) -> HTTPException:
    if "does not exist" in str(e) and "research_" in str(e):
        return HTTPException(status_code=503, detail={"code": "not_installed", "message": "Research missions are not installed yet (migration 41)."})
    logger.error(f"research missions query failed: {type(e).__name__}")
    return HTTPException(status_code=500, detail={"code": "error", "message": "Could not load research missions."})


class DraftMission(BaseModel):
    kind: str = Field(pattern="^(company|thematic)$")
    title: str = Field(min_length=3, max_length=200)
    question: str = Field(min_length=10, max_length=2000)
    domain: str = Field(min_length=2, max_length=60)
    params: Dict[str, Any] = Field(default_factory=dict)
    budget_usd: Optional[float] = Field(default=None, ge=0)


@research_missions_router.get("/missions")
def list_missions(status: Optional[str] = Query(None, pattern="^(draft|queued|running|completed|failed|stopped_budget)$"),
                  kind: Optional[str] = Query(None, pattern="^(company|thematic)$"), svc: ResearchMissionsService = Depends(get_research_missions_service)):
    try:
        return {"missions": svc.list_missions(status, kind), "domains": svc.domains()}
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e)


@research_missions_router.get("/missions/{mission_id}")
def get_mission(mission_id: int, svc: ResearchMissionsService = Depends(get_research_missions_service)):
    try:
        m = svc.get_mission(mission_id)
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e)
    if m is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such mission."})
    return m


@research_missions_router.post("/missions", status_code=201)
def create_mission(body: DraftMission, user: CurrentUser = Depends(require_owner), svc: ResearchMissionsService = Depends(get_research_missions_service)):
    try:
        return svc.create_draft(body.kind, body.title, body.question, body.domain, body.params, body.budget_usd, user.email)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid", "message": str(e)})
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e)


@research_missions_router.get("/budget")
def budget(svc: ResearchMissionsService = Depends(get_research_missions_service)):
    try:
        return svc.budget()
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e)
