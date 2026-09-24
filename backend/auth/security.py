# File: backend/auth/security.py
"""
Password hashing, JWT access tokens, and the raw secrets used by the login flow (refresh tokens, 2FA codes,
challenge tokens). Nothing here talks to the database or FastAPI - see store.py and dependencies.py.

Secrets are generated with `secrets` (CSPRNG) and only ever persisted as a sha256 hash, the same pattern
mechanism/alerts/access.py uses for Telegram invite codes: a stolen database row cannot be replayed as a
live credential.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import bcrypt
import jwt

CODE_LENGTH = 6
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False  # a malformed/foreign hash never matches, it never raises into a caller


def hash_secret(value: str) -> str:
    """sha256 hex digest - used for refresh tokens, challenge tokens and login codes before they touch the DB."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_login_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))


def generate_opaque_token() -> str:
    """A random token handed to the client (challenge token / refresh token). Only its hash is ever stored."""
    return secrets.token_urlsafe(32)


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set in .env - the auth system refuses to run without it.")
    return secret


@dataclass
class AccessTokenPayload:
    user_id: int
    role: str
    email: str


def create_access_token(payload: AccessTokenPayload, ttl_minutes: int) -> str:
    now = int(time.time())
    claims = {
        "sub": str(payload.user_id),
        "role": payload.role,
        "email": payload.email,
        "iat": now,
        "exp": now + ttl_minutes * 60,
    }
    return jwt.encode(claims, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[AccessTokenPayload]:
    try:
        claims = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    try:
        return AccessTokenPayload(user_id=int(claims["sub"]), role=claims["role"], email=claims["email"])
    except (KeyError, ValueError, TypeError):
        return None
