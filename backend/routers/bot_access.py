# File: backend/routers/bot_access.py
"""
Bot Access Control API: who may use the First Light private assistant (mechanism/alerts/access.py), surfaced on the
dashboard's /telegram page next to channel-post control. Owner-only (dashboard RBAC) - unlike channel posts this
never touches a public channel, so it does not additionally need TELEGRAM_CONTROL_TOKEN like telegram_control.py's
mutation endpoints do. See services/bot_access_service.py for the wiring; the rules themselves live in
mechanism/alerts/access.py, the same module the bot's own /approve /decline /revoke /invite commands call.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from services import bot_access_service as svc
from auth.dependencies import require_owner


class InviteRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=60)

logger = logging.getLogger(__name__)

bot_access_router = APIRouter(
    prefix="/api/bot-access",
    tags=["bot-access"],
    dependencies=[Depends(require_owner)],
    responses={404: {"description": "Not found"}},
)

router = bot_access_router


def _conn():
    from main import get_database_connection
    return get_database_connection()


@router.get("/overview")
def overview():
    """Access mode, member/revoked/open-invite counts, the waiting list, and the 30-day funnel."""
    try:
        return svc.overview(_conn)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "no_owner", "message": str(e)})


@router.post("/requests/{uid}/approve")
def approve_request(uid: int = Path(..., ge=1, le=999999999999999)):
    """Grant access and DM the person the same 'you're in' + guide message the bot sends when approved by chat."""
    try:
        result = svc.approve(_conn, uid)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "no_owner", "message": str(e)})
    if result["result"] == "owner":
        raise HTTPException(status_code=400, detail={"code": "is_owner", "message": "That id is the owner's own - the owner always has access."})
    if result["result"] != "approved":
        raise HTTPException(status_code=409, detail={"code": "no_request", "message": f"No waiting request for {uid} (already handled or expired)."})
    return result


@router.post("/requests/{uid}/decline")
def decline_request(uid: int = Path(..., ge=1, le=999999999999999)):
    try:
        result = svc.decline(_conn, uid)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "no_owner", "message": str(e)})
    if result["result"] != "declined":
        raise HTTPException(status_code=409, detail={"code": "no_request", "message": f"No waiting request for {uid} (already handled or expired)."})
    return result


@router.post("/members/{uid}/revoke")
def revoke_member(uid: int = Path(..., ge=1, le=999999999999999)):
    try:
        message = svc.revoke(_conn, uid)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "no_owner", "message": str(e)})
    return {"message": message}


@router.post("/invite")
def invite(body: InviteRequest):
    """A one-time, 72h invitation link (mirrors the bot's /invite command)."""
    try:
        return svc.create_invite(_conn, body.note)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "no_owner", "message": str(e)})
