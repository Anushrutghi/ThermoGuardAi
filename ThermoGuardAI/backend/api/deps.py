"""FastAPI dependencies: current user, RBAC, DB."""
from __future__ import annotations

import logging

import jwt as pyjwt
from fastapi import Depends, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.exceptions import ForbiddenError, UnauthorizedError
from backend.core.security import decode_access_token
from backend.db.session import get_db
from backend.models.user import User
from backend.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def decode_and_get_user(raw_token: str, db: Session) -> User:
    """Decode a JWT and load the active user it identifies.

    Shared by HTTP, WebSocket and token-query auth paths so the JWT-decode +
    active-user check never drifts between call sites. Raises UnauthorizedError.
    Malformed tokens (missing/foreign claims) are rejected the same way as
    expired ones — the raw token is never logged.

    With AUTH_BACKEND=firebase the app JWT ``sub`` is the Firebase uid and the
    user profile (role/organization) is loaded from the Firestore
    ``users/{uid}`` profile — never from client claims. Firebase proves
    identity; the backend remains the security boundary for everything else.
    """
    settings = get_settings()
    if settings.auth_uses_firebase:
        try:
            payload = decode_access_token(raw_token)
            uid = str(payload["sub"])
        except (pyjwt.PyJWTError, KeyError, TypeError) as exc:
            logger.warning("Authentication rejected: invalid or expired token")
            raise UnauthorizedError("Invalid or expired token") from exc
        from backend.firebase.users import get_profile

        profile = get_profile(uid)
        if profile is None or not profile.is_active:
            logger.warning("Authentication rejected: unknown or disabled Firebase user")
            raise UnauthorizedError("User not found or disabled")
        return profile  # type: ignore[return-value]
    try:
        payload = decode_access_token(raw_token)
        user_id = int(payload["sub"])
    except (pyjwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Authentication rejected: invalid or expired token")
        raise UnauthorizedError("Invalid or expired token") from exc
    user = UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        logger.warning("Authentication rejected: unknown or disabled user (id=%s)", user_id)
        raise UnauthorizedError("User not found or disabled")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise UnauthorizedError("Not authenticated")
    return decode_and_get_user(credentials.credentials, db)


def get_current_user_ws(websocket: WebSocket, db: Session = Depends(get_db)) -> User:
    """Authenticate a WebSocket connection via ?token= query param."""
    token = websocket.query_params.get("token")
    if not token:
        raise UnauthorizedError("Missing token")
    return decode_and_get_user(token, db)


def require_roles(*roles: str):
    """Dependency factory enforcing role-based access control."""

    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            logger.warning("Authorization denied: user=%s role=%s requires=%s", user.username, user.role, roles)
            raise ForbiddenError(f"Requires role(s): {', '.join(roles)}")
        return user

    return _checker
