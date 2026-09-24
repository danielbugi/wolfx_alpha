# File: backend/models/auth_models.py
"""
Auth Data Models - Request/Response structures for /api/auth
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
    OWNER = "owner"
    COLLABORATOR = "collaborator"


# REQUEST MODELS

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)


class VerifyCodeRequest(BaseModel):
    challenge_token: str
    code: str = Field(..., min_length=6, max_length=6)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    role: UserRole


# RESPONSE MODELS

class LoginChallengeResponse(BaseModel):
    stage: str = "2fa_required"
    challenge_token: str
    expires_in_minutes: int


class UserOut(BaseModel):
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: str
    last_login_at: Optional[str] = None


class SessionOut(BaseModel):
    id: int
    created_at: str
    expires_at: str
    revoked_at: Optional[str] = None
    user_agent: Optional[str] = None
    last_used_at: Optional[str] = None


class LoggedInResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class AccessTokenResponse(BaseModel):
    access_token: str


class UsersListResponse(BaseModel):
    users: List[UserOut]


class SessionsListResponse(BaseModel):
    sessions: List[SessionOut]
