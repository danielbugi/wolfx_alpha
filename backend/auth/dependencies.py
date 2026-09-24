# File: backend/auth/dependencies.py
"""
FastAPI auth dependencies, in the same style as backend/routers/telegram_control.py's require_control_token:
small functions raising HTTPException with a structured `detail`, attached per-router via
`dependencies=[Depends(...)]`. require_authenticated_user gates every existing router (screener, stock,
strategy, etc. - see main.py); require_owner additionally gates Telegram channel mutations and the
/api/auth/users admin endpoints.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from auth.security import AccessTokenPayload, decode_access_token
from auth.store import AuthStore

ROLE_OWNER = "owner"
ROLE_COLLABORATOR = "collaborator"


def get_auth_store() -> AuthStore:
    from main import get_database_connection  # local import: avoids a circular import at module load time
    return AuthStore(get_database_connection)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "not_authenticated", "message": message})


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Missing or malformed Authorization header.")
    return authorization[len("bearer "):].strip()


class CurrentUser:
    def __init__(self, id: int, email: str, role: str):
        self.id = id
        self.email = email
        self.role = role


def require_authenticated_user(
        authorization: Optional[str] = Header(default=None),
        store: AuthStore = Depends(get_auth_store),
) -> CurrentUser:
    """Every route that needs *any* signed-in user (Owner or Collaborator)."""
    token = _extract_bearer_token(authorization)
    payload: Optional[AccessTokenPayload] = decode_access_token(token)
    if payload is None:
        raise _unauthorized("The session token is missing, expired or invalid.")
    user = store.get_user_by_id(payload.user_id)
    if user is None or not user["is_active"]:
        raise _unauthorized("This account no longer has access.")
    return CurrentUser(id=user["id"], email=user["email"], role=user["role"])


def require_owner(current_user: CurrentUser = Depends(require_authenticated_user)) -> CurrentUser:
    """Telegram channel mutations and user management - Owner only."""
    if current_user.role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail={"code": "owner_only", "message": "Only the Owner can do this."})
    return current_user
