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

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from services import bot_access_service as svc
from auth.dependencies import require_owner


class InviteRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=60)


class SettingsRequest(BaseModel):
    mode: Optional[Literal["approve", "auto", "closed"]] = None
    max_members: Optional[int] = Field(None, ge=1, le=10000)

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


@router.get("/members")
def list_members(status: Literal["active", "revoked"] = Query("active")):
    """Members (or revoked members) with name, @username, joined/last-active, how they joined, tracked count, morning DM."""
    return {"members": svc.members(_conn, status)}


@router.post("/members/{uid}/restore")
def restore_member(uid: int = Path(..., ge=1, le=999999999999999)):
    """Let a revoked person back in, and DM them that they are in."""
    try:
        result = svc.restore(_conn, uid)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "no_owner", "message": str(e)})
    if result["result"] == "owner":
        raise HTTPException(status_code=400, detail={"code": "is_owner", "message": "That id is the owner's own - the owner always has access."})
    if result["result"] != "restored":
        raise HTTPException(status_code=409, detail={"code": "not_revoked", "message": f"{uid} is not a revoked member."})
    return result


@router.get("/audit")
def audit_log(limit: int = Query(50, ge=1, le=200)):
    """Who did what, newest first: invitations, approvals (manual / request / auto), revocations, mode and cap changes."""
    return {"entries": svc.audit(_conn, limit)}


@router.put("/settings")
def update_settings(body: SettingsRequest):
    """Switch the access mode and/or member cap. The bot applies it on the next request, without a restart."""
    if body.mode is None and body.max_members is None:
        raise HTTPException(status_code=400, detail={"code": "empty", "message": "Nothing to change."})
    try:
        return svc.update_settings(_conn, body.mode, body.max_members)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": "invalid", "message": str(e)})
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail={"code": "unavailable", "message": str(e)})
