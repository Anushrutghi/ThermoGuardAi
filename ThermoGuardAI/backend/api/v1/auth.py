"""Authentication routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.db.session import get_db
from backend.schemas.auth import FirebaseLoginRequest, LoginRequest, RegisterRequest, TokenResponse, UserOut
from backend.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> UserOut:
    user = AuthService(db).register(
        username=payload.username,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
    )
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token, user = AuthService(db).login(payload.username, payload.password)
    settings = get_settings()
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/firebase", response_model=TokenResponse)
def firebase_login(payload: FirebaseLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Exchange a verified Firebase ID token for an application session token.

    The client signs in with Firebase Authentication (email/password, Google,
    or phone OTP), sends the ID token here, and receives the same app JWT the
    rest of the platform uses (WS, downloads, REST). Role/organization are
    loaded server-side from the Firestore profile — never from client claims.
    """
    token, profile = AuthService(db).firebase_login(payload.id_token)
    settings = get_settings()
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=UserOut.model_validate(profile),
    )
