# File: backend/routers/telegram_control.py
"""
Telegram Control Center API: what First Light posted to the channels, and (with the control token) edit / delete / pin / compose new posts.

Reading is open like the rest of this API by default; set TELEGRAM_READ_REQUIRES_TOKEN=1 to require the same token for reads too (off by default
so today's localhost browsing keeps working unchanged - see require_read_access). Every change needs the header `X-Control-Token` equal to
TELEGRAM_CONTROL_TOKEN from .env, and is refused outright while that variable is unset (fail closed: this API can post to and alter a public
channel, so it must never be reachable without a secret). The rules themselves are in mechanism/alerts/channel_control.py
(see services/telegram_control_service.py).
"""

import hmac
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services.telegram_control_service import build_control
from alerts.channel_control import ChannelControl, ControlError, MAX_UPLOAD_BYTES, control_token
from auth.dependencies import require_authenticated_user, require_owner

logger = logging.getLogger(__name__)

telegram_control_router = APIRouter(
    prefix="/api/telegram",
    tags=["telegram-control"],
    responses={404: {"description": "Not found"}},
)

WRONG_TOKEN_DELAY_SECONDS = 0.5      # slows guessing; the token is long and random, so this is only a speed bump
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


def get_control() -> ChannelControl:
    from main import get_database_connection
    return build_control(get_database_connection)


def _check_token(x_control_token: Optional[str]) -> None:
    expected = control_token()          # the same rule as the overview: unset or a placeholder = every change is refused
    if not expected:
        raise HTTPException(status_code=403, detail={"code": "controls_disabled", "message": "This action is disabled: set TELEGRAM_CONTROL_TOKEN in .env and restart the API."})
    if not x_control_token or not hmac.compare_digest(x_control_token.encode("utf-8"), expected.encode("utf-8")):
        time.sleep(WRONG_TOKEN_DELAY_SECONDS)
        raise HTTPException(status_code=401, detail={"code": "bad_token", "message": "The control token is missing or wrong."})


def require_control_token(x_control_token: Optional[str] = Header(default=None), _owner=Depends(require_owner)) -> None:
    """Every action that changes a channel (edit, delete, pin, unpin, compose, replace-photo): must be the
    Owner (RBAC) AND hold the control token (the pre-existing secret, kept as defense-in-depth)."""
    _check_token(x_control_token)


def require_read_access(x_control_token: Optional[str] = Header(default=None), _user=Depends(require_authenticated_user)) -> None:
    """Reads need any signed-in user (Owner or Collaborator). The control token additionally gates reads only
    if TELEGRAM_READ_REQUIRES_TOKEN=1 (default off; see CLAUDE.md/TELEGRAM_CONTROL_MILESTONES.md)."""
    if os.getenv("TELEGRAM_READ_REQUIRES_TOKEN", "").strip() == "1":
        _check_token(x_control_token)


def _refused(e: ControlError) -> HTTPException:
    return HTTPException(status_code=e.http, detail={"code": e.code, "message": e.message, "warnings": e.warnings})


async def _read_upload(photo: UploadFile) -> bytes:
    if photo.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=422, detail={"code": "bad_image", "message": f"Unsupported image type {photo.content_type!r}; use PNG, JPEG or WEBP.", "warnings": []})
    data = await photo.read()
    if not data:
        raise HTTPException(status_code=422, detail={"code": "empty", "message": "The image is empty.", "warnings": []})
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"code": "too_long", "message": f"The image is {len(data) / 1_000_000:.1f} MB; Telegram allows up to {MAX_UPLOAD_BYTES // 1_000_000} MB.", "warnings": []})
    return data


class EditRequest(BaseModel):
    text: str = Field(..., max_length=20000, description="The new HTML text (or photo caption), exactly as Telegram should show it")
    acknowledge_wording: bool = Field(False, description="True = the owner saw the wording-guard warning and wants it anyway")


class ComposeRequest(BaseModel):
    target: str = Field(..., description="dev | prod")
    text: str = Field(..., max_length=20000)
    kind: Optional[str] = Field(None, max_length=30)
    silent: bool = Field(True, description="No notification sound")
    disable_preview: bool = Field(True, description="No link preview card")
    acknowledge_wording: bool = Field(False)


router = telegram_control_router


@router.get("/overview", dependencies=[Depends(require_read_access)])
def overview(control: ChannelControl = Depends(get_control)):
    """Per-channel counts, the lock state, the system configuration that governs posting, the kinds of post seen so far, and warnings."""
    return control.overview()


@router.get("/schedule", dependencies=[Depends(require_read_access)])
def schedule(target: str = Query(..., description="dev | prod"), control: ChannelControl = Depends(get_control)):
    """What is configured to post today for `target`, and whether it already has (see mechanism/alerts/schedule.py)."""
    try:
        return control.schedule(target)
    except ControlError as e:
        raise _refused(e)


@router.get("/messages", dependencies=[Depends(require_read_access)])
def list_messages(
        target: Optional[str] = Query(None, description="dev | prod"),
        kind: Optional[str] = Query(None, max_length=30),
        status: Optional[str] = Query(None, description="sent | edited | deleted"),
        day: Optional[str] = Query(None, description="YYYY-MM-DD in the configured timezone"),
        q: Optional[str] = Query(None, max_length=100, description="text search"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        control: ChannelControl = Depends(get_control),
):
    try:
        return control.list_messages(target=target, kind=kind, status=status, day=day, q=q, limit=limit, offset=offset)
    except ControlError as e:
        raise _refused(e)


@router.get("/messages/{ledger_id}", dependencies=[Depends(require_read_access)])
def get_message(ledger_id: int = Path(..., ge=1), control: ChannelControl = Depends(get_control)):
    """One message with its full text, the text it was first sent with, its buttons and the audit trail of what this page did to it."""
    try:
        return control.get_message(ledger_id)
    except ControlError as e:
        raise _refused(e)


@router.post("/messages", dependencies=[Depends(require_control_token)])
def compose_text(body: ComposeRequest, control: ChannelControl = Depends(get_control)):
    """Compose and send a brand-new TEXT post to a channel."""
    try:
        return control.compose(body.target, body.text, kind=body.kind, silent=body.silent, disable_preview=body.disable_preview,
                               acknowledge_wording=body.acknowledge_wording)
    except ControlError as e:
        raise _refused(e)


@router.post("/messages/photo", dependencies=[Depends(require_control_token)])
async def compose_photo(
        target: str = Form(..., description="dev | prod"),
        caption: str = Form(""),
        kind: Optional[str] = Form(None),
        silent: bool = Form(True),
        acknowledge_wording: bool = Form(False),
        photo: UploadFile = File(...),
        control: ChannelControl = Depends(get_control),
):
    """Compose and send a brand-new PHOTO post to a channel."""
    data = await _read_upload(photo)
    # control.compose_photo does blocking psycopg2 queries and a `requests`-based Telegram call whose retry loop uses real time.sleep()
    # (up to ~60s per 429, several attempts) - run it off the event loop so a slow Telegram round trip cannot stall every other request
    # this process is serving, exactly like every other endpoint here that only needed a plain `def` for Starlette to threadpool it.
    try:
        return await run_in_threadpool(control.compose_photo, target, data, caption=caption, kind=kind, silent=silent,
                                       acknowledge_wording=acknowledge_wording)
    except ControlError as e:
        raise _refused(e)


@router.patch("/messages/{ledger_id}", dependencies=[Depends(require_control_token)])
def edit_message(body: EditRequest, ledger_id: int = Path(..., ge=1), control: ChannelControl = Depends(get_control)):
    try:
        return control.edit(ledger_id, body.text, acknowledge_wording=body.acknowledge_wording)
    except ControlError as e:
        raise _refused(e)


@router.patch("/messages/{ledger_id}/photo", dependencies=[Depends(require_control_token)])
async def replace_photo(
        ledger_id: int = Path(..., ge=1),
        caption: str = Form(""),
        acknowledge_wording: bool = Form(False),
        photo: UploadFile = File(...),
        control: ChannelControl = Depends(get_control),
):
    """Replace an existing photo message's image (and its caption, resent in the same call - Telegram's editMessageMedia has no
    "keep the old caption" option, so this endpoint never guesses one)."""
    data = await _read_upload(photo)
    try:                                          # see compose_photo's comment: offloaded for the same reason
        return await run_in_threadpool(control.replace_photo, ledger_id, data, caption, acknowledge_wording=acknowledge_wording)
    except ControlError as e:
        raise _refused(e)


@router.delete("/messages/{ledger_id}", dependencies=[Depends(require_control_token)])
def delete_message(ledger_id: int = Path(..., ge=1), control: ChannelControl = Depends(get_control)):
    try:
        return control.delete(ledger_id)
    except ControlError as e:
        raise _refused(e)


@router.post("/messages/{ledger_id}/pin", dependencies=[Depends(require_control_token)])
def pin_message(ledger_id: int = Path(..., ge=1), control: ChannelControl = Depends(get_control)):
    try:
        return control.pin(ledger_id)
    except ControlError as e:
        raise _refused(e)


@router.post("/messages/{ledger_id}/unpin", dependencies=[Depends(require_control_token)])
def unpin_message(ledger_id: int = Path(..., ge=1), control: ChannelControl = Depends(get_control)):
    try:
        return control.unpin(ledger_id)
    except ControlError as e:
        raise _refused(e)
