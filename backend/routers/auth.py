# File: backend/routers/auth.py
"""
/api/auth - login (password + emailed 2FA code), refresh, logout, and Owner-only user/session management.

Flow: POST /login (password checked) -> creates a dashboard_login_challenges row, emails a 6-digit code,
returns an opaque challenge_token (the client never sees the code's hash or the row id) -> POST /verify-2fa
(code checked against the challenge) -> issues a short-lived JWT access token plus a long-lived refresh
token (stored hashed in dashboard_sessions, so the Owner can revoke it from /users at any time - see
DELETE /sessions/{id}). Every step is written to dashboard_auth_audit (mechanism/alerts/access.py's
fail-closed / hash-never-store-plaintext / audit-everything philosophy, applied here).
"""
import logging
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user, require_owner
from auth.email_service import EmailSendError, send_login_code
from auth.security import (
    AccessTokenPayload,
    create_access_token,
    generate_login_code,
    generate_opaque_token,
    hash_password,
    hash_secret,
    verify_password,
)
from auth.store import AuthStore, utcnow
from models.auth_models import (
    AccessTokenResponse,
    CreateUserRequest,
    LoggedInResponse,
    LoginChallengeResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SessionOut,
    SessionsListResponse,
    UserOut,
    UsersListResponse,
    VerifyCodeRequest,
)

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"], responses={404: {"description": "Not found"}})

WRONG_PASSWORD_DELAY_SECONDS = 0.5
WRONG_CODE_DELAY_SECONDS = 0.5


def _access_ttl_minutes() -> int:
    return int(os.environ.get("ACCESS_TOKEN_TTL_MINUTES", "30"))


def _refresh_ttl_days() -> int:
    return int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "30"))


def _code_ttl_minutes() -> int:
    return int(os.environ.get("LOGIN_CODE_TTL_MINUTES", "10"))


def _max_code_attempts() -> int:
    return int(os.environ.get("MAX_LOGIN_CODE_ATTEMPTS", "5"))


def _iso(value) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else value


def _user_out(user: dict) -> UserOut:
    return UserOut(
        id=user["id"], email=user["email"], role=user["role"], is_active=user["is_active"],
        created_at=_iso(user["created_at"]), last_login_at=_iso(user.get("last_login_at")),
    )


@auth_router.post("/login", response_model=LoginChallengeResponse)
def login(body: LoginRequest, store: AuthStore = Depends(get_auth_store)):
    user = store.get_user_by_email(body.email)
    if user is None or not user["is_active"] or not verify_password(body.password, user["password_hash"]):
        if user is not None:
            store.audit("login_password_fail", user_id=user["id"])
        time.sleep(WRONG_PASSWORD_DELAY_SECONDS)
        raise HTTPException(status_code=401, detail={"code": "bad_credentials", "message": "Wrong email or password."})

    store.audit("login_password_ok", user_id=user["id"])

    code = generate_login_code()
    challenge_token = generate_opaque_token()
    ttl_minutes = _code_ttl_minutes()
    store.create_login_challenge(user["id"], hash_secret(challenge_token), hash_secret(code), ttl_minutes)

    try:
        send_login_code(user["email"], code, ttl_minutes)
    except EmailSendError:
        raise HTTPException(status_code=502, detail={"code": "email_failed", "message": "Could not send the sign-in code email. Try again shortly."})

    return LoginChallengeResponse(challenge_token=challenge_token, expires_in_minutes=ttl_minutes)


@auth_router.post("/verify-2fa", response_model=LoggedInResponse)
def verify_2fa(body: VerifyCodeRequest, user_agent: Optional[str] = Header(default=None), store: AuthStore = Depends(get_auth_store)):
    challenge = store.get_challenge_by_token_hash(hash_secret(body.challenge_token))
    if challenge is None:
        raise HTTPException(status_code=401, detail={"code": "bad_challenge", "message": "This sign-in attempt is invalid or already used."})

    if challenge["consumed_at"] is not None or challenge["expires_at"] < utcnow():
        raise HTTPException(status_code=401, detail={"code": "expired", "message": "This code has expired. Sign in again."})

    if challenge["attempts"] >= _max_code_attempts():
        store.audit("login_locked", user_id=challenge["user_id"])
        raise HTTPException(status_code=423, detail={"code": "too_many_attempts", "message": "Too many wrong codes. Sign in again to get a new one."})

    if hash_secret(body.code) != challenge["code_hash"]:
        store.increment_challenge_attempts(challenge["id"])
        store.audit("login_code_fail", user_id=challenge["user_id"])
        time.sleep(WRONG_CODE_DELAY_SECONDS)
        raise HTTPException(status_code=401, detail={"code": "bad_code", "message": "Wrong code."})

    store.consume_challenge(challenge["id"])
    store.audit("login_code_ok", user_id=challenge["user_id"])

    user = store.get_user_by_id(challenge["user_id"])
    if user is None or not user["is_active"]:
        raise HTTPException(status_code=401, detail={"code": "inactive", "message": "This account no longer has access."})

    store.touch_last_login(user["id"])
    access_token = create_access_token(AccessTokenPayload(user_id=user["id"], role=user["role"], email=user["email"]), _access_ttl_minutes())
    refresh_token = generate_opaque_token()
    store.create_session(user["id"], hash_secret(refresh_token), _refresh_ttl_days(), user_agent)

    return LoggedInResponse(access_token=access_token, refresh_token=refresh_token, user=_user_out(user))


@auth_router.post("/refresh", response_model=AccessTokenResponse)
def refresh(body: RefreshRequest, store: AuthStore = Depends(get_auth_store)):
    session = store.get_session_by_token_hash(hash_secret(body.refresh_token))
    if session is None or session["revoked_at"] is not None or session["expires_at"] < utcnow():
        raise HTTPException(status_code=401, detail={"code": "bad_session", "message": "Session is invalid or has been signed out. Sign in again."})

    user = store.get_user_by_id(session["user_id"])
    if user is None or not user["is_active"]:
        raise HTTPException(status_code=401, detail={"code": "inactive", "message": "This account no longer has access."})

    store.touch_session(session["id"])
    store.audit("refresh", user_id=user["id"])
    access_token = create_access_token(AccessTokenPayload(user_id=user["id"], role=user["role"], email=user["email"]), _access_ttl_minutes())
    return AccessTokenResponse(access_token=access_token)


@auth_router.post("/logout")
def logout(body: LogoutRequest, store: AuthStore = Depends(get_auth_store)):
    session = store.get_session_by_token_hash(hash_secret(body.refresh_token))
    if session is not None:
        store.revoke_session(session["id"])
        store.audit("logout", user_id=session["user_id"])
    return {"ok": True}


@auth_router.get("/me", response_model=UserOut)
def me(current_user: CurrentUser = Depends(require_authenticated_user), store: AuthStore = Depends(get_auth_store)):
    user = store.get_user_by_id(current_user.id)
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Session user no longer exists."})
    return _user_out(user)


# --- Owner-only user + session management ------------------------------------

@auth_router.get("/users", response_model=UsersListResponse, dependencies=[Depends(require_owner)])
def list_users(store: AuthStore = Depends(get_auth_store)):
    return UsersListResponse(users=[_user_out(u) for u in store.list_users()])


@auth_router.post("/users", response_model=UserOut, dependencies=[Depends(require_owner)])
def create_user(body: CreateUserRequest, current_user: CurrentUser = Depends(require_owner), store: AuthStore = Depends(get_auth_store)):
    if store.get_user_by_email(body.email) is not None:
        raise HTTPException(status_code=409, detail={"code": "email_taken", "message": "An account with this email already exists."})
    new_id = store.create_user(body.email, hash_password(body.password), body.role.value)
    store.audit("user_created", user_id=new_id, actor_user_id=current_user.id, detail=body.role.value)
    return _user_out(store.get_user_by_id(new_id))


@auth_router.patch("/users/{user_id}/deactivate", response_model=UserOut, dependencies=[Depends(require_owner)])
def deactivate_user(user_id: int, current_user: CurrentUser = Depends(require_owner), store: AuthStore = Depends(get_auth_store)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail={"code": "self", "message": "You cannot deactivate your own account."})
    target = store.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such user."})
    store.set_user_active(user_id, False)
    store.audit("user_deactivated", user_id=user_id, actor_user_id=current_user.id)
    return _user_out(store.get_user_by_id(user_id))


@auth_router.patch("/users/{user_id}/reactivate", response_model=UserOut, dependencies=[Depends(require_owner)])
def reactivate_user(user_id: int, current_user: CurrentUser = Depends(require_owner), store: AuthStore = Depends(get_auth_store)):
    target = store.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such user."})
    store.set_user_active(user_id, True)
    store.audit("user_reactivated", user_id=user_id, actor_user_id=current_user.id)
    return _user_out(store.get_user_by_id(user_id))


@auth_router.get("/users/{user_id}/sessions", response_model=SessionsListResponse, dependencies=[Depends(require_owner)])
def list_user_sessions(user_id: int, store: AuthStore = Depends(get_auth_store)):
    sessions = store.list_sessions_for_user(user_id)
    return SessionsListResponse(sessions=[
        SessionOut(id=s["id"], created_at=_iso(s["created_at"]), expires_at=_iso(s["expires_at"]),
                   revoked_at=_iso(s.get("revoked_at")), user_agent=s.get("user_agent"), last_used_at=_iso(s.get("last_used_at")))
        for s in sessions
    ])


@auth_router.delete("/users/{user_id}/sessions/{session_id}", dependencies=[Depends(require_owner)])
def revoke_user_session(user_id: int, session_id: int, current_user: CurrentUser = Depends(require_owner), store: AuthStore = Depends(get_auth_store)):
    session = store.get_session_by_id_for_user(session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "No such session."})
    store.revoke_session(session_id)
    store.audit("session_revoked", user_id=user_id, actor_user_id=current_user.id)
    return {"ok": True}
