"""Authentication and user schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from backend.schemas.common import ORMModel


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=128)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserOut(ORMModel):
    # Firebase profiles key users by uid (string); local users by int id.
    # email is nullable: phone-only Firebase users have no email address.
    id: int | str
    username: str
    email: str | None = None
    full_name: str | None = None
    role: str
    organization: str | None = None  # S2 data isolation scope
    is_active: bool
    created_at: datetime


class FirebaseLoginRequest(BaseModel):
    """Exchange a verified Firebase ID token for an application session token."""

    id_token: str = Field(min_length=20, max_length=8192)


class UserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    role: str | None = Field(default=None, pattern="^(admin|technician|viewer)$")
    is_active: bool | None = None


TokenResponse.model_rebuild()
