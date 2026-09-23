"""Authentication service: register, login, JWT issuance."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.exceptions import ConflictError, UnauthorizedError
from backend.core.security import create_access_token, hash_password, verify_password
from backend.firebase.users import FirebaseUserProfile
from backend.models.user import User
from backend.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

VALID_ROLES = ("admin", "technician", "viewer")


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def register(self, username: str, email: str, password: str, full_name: str | None = None, role: str = "viewer") -> User:
        if get_settings().auth_uses_firebase:
            # Firebase Authentication owns identity; self-signup happens on the
            # client and is provisioned server-side with role=viewer, org=None.
            raise ConflictError("Self-registration is disabled — sign up with Firebase Authentication")
        if self.users.get_by_username(username):
            raise ConflictError("Username already taken")
        if self.users.get_by_email(email):
            raise ConflictError("Email already registered")
        if role not in VALID_ROLES:
            role = "viewer"
        return self.users.create(
            username=username,
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role=role,
        )

    def authenticate(self, username: str, password: str) -> User:
        user = self.users.get_by_username(username)
        if user is None or not verify_password(password, user.hashed_password):
            # Username only — never the password, never the hashed value.
            logger.warning("Authentication failure for username=%s", username)
            raise UnauthorizedError("Invalid username or password")
        if not user.is_active:
            logger.warning("Authentication denied for disabled account username=%s", username)
            raise UnauthorizedError("Account is disabled")
        return user

    def login(self, username: str, password: str) -> tuple[str, User]:
        if get_settings().auth_uses_firebase:
            raise UnauthorizedError("Sign in with Firebase Authentication")
        user = self.authenticate(username, password)
        token = create_access_token(user.id, extra={"role": user.role, "username": user.username})
        logger.info("User %s logged in (id=%s role=%s)", user.username, user.id, user.role)
        return token, user

    def firebase_login(self, id_token: str) -> tuple[str, FirebaseUserProfile]:
        """Exchange a verified Firebase ID token for an application session token.

        Identity comes from Firebase; role/organization come ONLY from the
        Firestore profile (provisioned server-side). No client-supplied
        organization or role is ever trusted.
        """
        settings = get_settings()
        if not settings.auth_uses_firebase:
            raise UnauthorizedError("Firebase authentication is not enabled")
        from backend.firebase.auth import verify_firebase_id_token
        from backend.firebase.users import provision_on_login

        claims = verify_firebase_id_token(id_token)
        profile = provision_on_login(claims)
        token = create_access_token(profile.id, extra={"role": profile.role, "username": profile.username})
        logger.info("User %s authenticated via Firebase (uid=%s role=%s)", profile.username, profile.id[:12], profile.role)
        return token, profile
