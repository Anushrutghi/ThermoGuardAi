"""Pytest fixtures — isolated test database + API client."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Test environment before importing app modules
#
# These MUST be hard assignments, not setdefault: if the caller's shell exports
# DATABASE_URL (or another var) pointing at the real project database, setdefault
# silently keeps it and the whole suite would read/write the production DB
# (this happened: tests polluted database/thermoguard.db). The suite is always
# isolated to its own throwaway SQLite file.
_TEST_DB = ROOT / "database" / "test_thermoguard.db"
if _TEST_DB.exists():  # fresh DB per test session
    _TEST_DB.unlink()
    for suffix in ("-wal", "-shm"):
        (ROOT / "database" / f"test_thermoguard.db{suffix}").unlink(missing_ok=True)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite:///./database/test_thermoguard.db"
os.environ["DETECTOR_MODE"] = "fallback"
os.environ["THERMAL_MODE"] = "simulator"


@pytest.fixture()
def test_db() -> object:
    """A fresh database session for unit tests."""
    from backend.db.session import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture()
def client() -> object:
    """FastAPI TestClient with seeded defaults."""
    from fastapi.testclient import TestClient

    from backend.db.seed import seed_defaults
    from backend.main import app

    seed_defaults()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client) -> dict[str, str]:  # noqa: ANN001
    """Login as the seeded admin and return auth headers."""
    from backend.core.config import get_settings

    settings = get_settings()
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.seed_admin_username, "password": settings.seed_admin_password},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
