"""Lazy, emulator-aware Firebase Admin SDK client (S6)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from backend.core.config import get_settings

logger = logging.getLogger(__name__)

_app = None


def _configure_emulator_env() -> None:
    """Point the Admin SDK at the local emulators (development/tests only)."""
    settings = get_settings()
    mappings = {
        "FIRESTORE_EMULATOR_HOST": settings.firestore_emulator_host,
        "FIREBASE_AUTH_EMULATOR_HOST": settings.firebase_auth_emulator_host,
        "FIREBASE_STORAGE_EMULATOR_HOST": settings.firebase_storage_emulator_host,
    }
    for env_name, host in mappings.items():
        if host:
            os.environ.setdefault(env_name, host)


def get_app():
    """Return the initialized Firebase Admin SDK app (lazy singleton)."""
    global _app
    if _app is not None:
        return _app
    settings = get_settings()
    if not settings.firebase_enabled:
        raise RuntimeError("Firebase is not enabled (set AUTH_BACKEND/STORAGE_BACKEND or APP_ENV=production)")
    _configure_emulator_env()

    import firebase_admin
    from firebase_admin import credentials

    cred = None
    if settings.firebase_service_account_path:
        path = Path(settings.firebase_service_account_path)
        if path.is_file():
            cred = credentials.Certificate(str(path))
    if cred is None:
        env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if env_path and Path(env_path).is_file():
            cred = credentials.Certificate(env_path)

    options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
    try:
        _app = firebase_admin.initialize_app(cred, options=options)
    except ValueError:
        # "The default Firebase app already exists" (tests/repeated calls)
        _app = firebase_admin.get_app()
    logger.info(
        "Firebase Admin SDK initialized (project=%s emulator=%s)",
        settings.firebase_project_id or "default",
        settings.firebase_emulator,
    )
    return _app


def get_firestore():
    """Return the Firestore admin client for the active project."""
    from firebase_admin import firestore

    return firestore.client(get_app())


def get_storage_bucket():
    """Return the Firebase Storage bucket (for report files)."""
    from firebase_admin import storage

    settings = get_settings()
    name = settings.firebase_storage_bucket or None
    return storage.bucket(name=name, app=get_app())
