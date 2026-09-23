"""Server-side Firebase ID-token verification (S6).

Firebase Authentication proves identity; it NEVER grants organization access.
Role/organization are loaded only from the Firestore user profile (see
``backend.firebase.users``) — never from client claims or client-supplied
organization IDs.
"""
from __future__ import annotations

import logging

from backend.core.config import get_settings
from backend.core.exceptions import UnauthorizedError

logger = logging.getLogger(__name__)


def verify_firebase_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return its claims.

    Raises UnauthorizedError for invalid, expired, revoked or malformed tokens.
    The raw token is never logged. Revocation checks are skipped when running
    against the emulator (which does not support them).
    """
    settings = get_settings()
    import firebase_admin.auth as fauth

    check_revoked = settings.firebase_check_revoked and not settings.firebase_emulator
    try:
        return fauth.verify_id_token(id_token, check_revoked=check_revoked)
    except UnauthorizedError:
        raise
    except Exception as exc:  # noqa: BLE001 — map every SDK failure to 401
        logger.warning("Firebase authentication rejected: %s", type(exc).__name__)
        raise UnauthorizedError("Invalid or expired Firebase token") from exc
