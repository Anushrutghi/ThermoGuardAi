"""Production hardening (S6 Phase 2): firestore mode must never touch SQLite.

These tests prove the application cannot silently fall back to a local
SQLite/PostgreSQL database when Firebase persistence is active.
"""
from __future__ import annotations

import pytest

from backend.core.config import Settings, get_settings


@pytest.fixture()
def firestore_settings(monkeypatch) -> Settings:  # noqa: ANN001
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "firestore")
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    return settings


def test_session_local_forbidden_in_firestore_mode(firestore_settings) -> None:  # noqa: ANN001
    from backend.db.session import SessionLocal

    with pytest.raises(RuntimeError, match="SessionLocal is forbidden"):
        SessionLocal()


def test_engine_forbidden_in_firestore_mode(firestore_settings) -> None:  # noqa: ANN001
    from backend.db.session import _get_engine

    with pytest.raises(RuntimeError, match="engine is forbidden"):
        _get_engine()


def test_get_db_yields_firestore_session(firestore_settings, monkeypatch) -> None:  # noqa: ANN001
    from tests.fakes.fake_firestore import FakeFirestore

    fake = FakeFirestore()
    monkeypatch.setattr("backend.firebase.client.get_firestore", lambda: fake)

    from backend.db.session import get_db
    from backend.firebase.engine import FirestoreSession

    gen = get_db()
    db = next(gen)
    try:
        assert isinstance(db, FirestoreSession)
    finally:
        gen.close()


def test_open_session_yields_firestore_session(firestore_settings, monkeypatch) -> None:  # noqa: ANN001
    from tests.fakes.fake_firestore import FakeFirestore

    fake = FakeFirestore()
    monkeypatch.setattr("backend.firebase.client.get_firestore", lambda: fake)

    from backend.db.session import open_session
    from backend.firebase.engine import FirestoreSession

    db, close = open_session()
    try:
        assert isinstance(db, FirestoreSession)
    finally:
        close()


def test_production_validation_rejects_sqlite_backend() -> None:
    settings = Settings(
        app_env="production",
        debug=False,
        secret_key="x" * 40,
        cors_origins="https://dashboard.example.com",
        allowed_hosts="dashboard.example.com",
        auth_backend="local",
        storage_backend="sqlite",
        firebase_project_id="thermoguardai",
        firebase_storage_bucket="thermoguardai.firebasestorage.app",
    )
    with pytest.raises(RuntimeError, match="AUTH_BACKEND must be 'firebase'"):
        settings.validate_security()
    settings2 = Settings(
        app_env="production",
        debug=False,
        secret_key="x" * 40,
        cors_origins="https://dashboard.example.com",
        allowed_hosts="dashboard.example.com",
        auth_backend="firebase",
        storage_backend="sqlite",
        firebase_project_id="thermoguardai",
        firebase_storage_bucket="thermoguardai.firebasestorage.app",
    )
    with pytest.raises(RuntimeError, match="STORAGE_BACKEND must be 'firestore'"):
        settings2.validate_security()


def test_production_validation_requires_firebase_credentials() -> None:
    settings = Settings(
        app_env="production",
        debug=False,
        secret_key="x" * 40,
        cors_origins="https://dashboard.example.com",
        allowed_hosts="dashboard.example.com",
        auth_backend="firebase",
        storage_backend="firestore",
        firebase_project_id="thermoguardai",
        firebase_service_account_path="/nonexistent/sa.json",
    )
    import os

    saved = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    try:
        with pytest.raises(RuntimeError, match="service-account"):
            settings.validate_security()
    finally:
        if saved:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = saved
