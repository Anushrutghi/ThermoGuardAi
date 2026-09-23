"""S6 end-to-end flow test — Firebase auth + Firestore + Storage (offline).

Runs the real FastAPI application with AUTH_BACKEND=firebase and
STORAGE_BACKEND=firestore, backed by the in-memory FakeFirestore / fake bucket
and a mocked Admin SDK. The local database is never touched (init/seed are
skipped by the lifespan in firestore mode).

Verified here: ID-token exchange → app JWT, server-side role/org from the
profile, organization isolation (list + direct-ID 404), 401 on missing/garbage
tokens, Firestore persistence of a generated report, report file upload to
Firebase Storage, authorized download, and safe report metadata (boolean
``file_available``, no filesystem path).
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import firebase_admin.auth as fauth
import pytest
from fastapi.testclient import TestClient

from backend.core.config import get_settings
from tests.fakes.fake_firestore import FakeFirestore, FakeStorageBucket

# Realistic-length fake ID tokens (the schema requires >= 20 chars).
FAKE_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJ1aWQiOiJ1LWZsb3cifQ.signature"
FAKE_TOKEN_FOREIGN = "eyJhbGciOiJSUzI1NiJ9.eyJ1aWQiOiJ1LWZvcmVpZ24ifQ.signature"


@pytest.fixture
def firebase_env(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    monkeypatch.setattr(settings, "storage_backend", "firestore")
    monkeypatch.setattr(settings, "firebase_project_id", "thermoguardai")
    monkeypatch.setattr(settings, "firebase_storage_bucket", "thermoguardai.appspot.com")
    fake = FakeFirestore()
    bucket = FakeStorageBucket()
    monkeypatch.setattr("backend.firebase.client.get_firestore", lambda: fake)
    monkeypatch.setattr("backend.firebase.client.get_storage_bucket", lambda: bucket)

    def _get_user_by_email(email):  # noqa: ANN001
        raise fauth.UserNotFoundError(email)

    monkeypatch.setattr(fauth, "get_user_by_email", _get_user_by_email)
    monkeypatch.setattr(fauth, "create_user", lambda **kw: SimpleNamespace(uid="seed-admin-uid"))
    def _verify(tok: str) -> dict:  # token-aware so tests can act as other users
        if tok == FAKE_TOKEN_FOREIGN:
            return {"uid": "u-foreign", "email": "foreign@other.io", "name": "Foreign", "firebase": {"sign_in_provider": "password"}}
        return {"uid": "u-flow", "email": "flow@thermoguard.io", "name": "Flow Tester", "firebase": {"sign_in_provider": "password"}}

    monkeypatch.setattr("backend.firebase.auth.verify_firebase_id_token", _verify)
    return fake, bucket


def _exchange(client: TestClient) -> tuple[str, str]:
    resp = client.post("/api/v1/auth/firebase", json={"id_token": FAKE_TOKEN})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user"]["role"] == "viewer"
    assert data["user"]["organization"] is None  # identity ≠ org access
    return data["access_token"], data["user"]["id"]


def test_firebase_auth_and_organization_isolation(firebase_env) -> None:  # noqa: ANN001
    fake, _bucket = firebase_env
    from backend.main import app

    with TestClient(app) as client:
        token, uid = _exchange(client)
        # role/org are granted server-side only
        from backend.firebase.users import update_profile

        update_profile(uid, role="admin", organization="ThermoGuard")

        now = datetime.now(UTC)
        for dev_id, name, org in ((1, "Owned Device", "ThermoGuard"), (2, "Foreign Device", "OtherCo")):
            fake.seed(
                "devices",
                str(dev_id),
                {
                    "id": dev_id,
                    "name": name,
                    "organization": org,
                    "derived_status": "ACTIVE",
                    "status_override": False,
                    "status": "active",
                    "risk_level": "LOW",
                    "device_type": "wall_switch",
                    "location": "Room 1",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )

        headers = {"Authorization": f"Bearer {token}"}
        # org-scoped listing
        resp = client.get("/api/v1/devices", headers=headers)
        assert resp.status_code == 200, resp.text
        assert [d["name"] for d in resp.json()] == ["Owned Device"]
        # cross-org direct-ID access → 404 (no existence leak)
        assert client.get("/api/v1/devices/2", headers=headers).status_code == 404
        # own device visible
        assert client.get("/api/v1/devices/1", headers=headers).status_code == 200
        # unauthenticated / garbage token → 401
        assert client.get("/api/v1/devices").status_code == 401
        assert client.get("/api/v1/devices", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_firebase_team_endpoint_lists_uid_profiles(firebase_env) -> None:  # noqa: ANN001
    """The Team (users) endpoint reads Firestore profiles keyed by uid.

    Firebase uids are non-numeric strings; the Firestore shim must return them
    as-is instead of trying to int()-cast the document id.
    """
    fake, _bucket = firebase_env
    from backend.main import app

    with TestClient(app) as client:
        token, uid = _exchange(client)
        from backend.firebase.users import update_profile

        update_profile(uid, role="admin", organization="ThermoGuard")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/api/v1/users", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] >= 1
        assert any(u["id"] == uid for u in data["items"]), data


def test_firebase_report_storage_and_authorized_download(firebase_env) -> None:  # noqa: ANN001
    fake, bucket = firebase_env
    from backend.main import app

    with TestClient(app) as client:
        token, uid = _exchange(client)
        from backend.firebase.users import update_profile

        update_profile(uid, role="technician", organization="ThermoGuard")
        headers = {"Authorization": f"Bearer {token}"}

        now = datetime.now(UTC)
        fake.seed("panels", "1", {"id": 1, "name": "Panel A", "code": "PANEL-A", "location": "Room 1", "organization": "ThermoGuard"})
        fake.seed(
            "devices",
            "1",
            {
                "id": 1,
                "name": "Owned Device",
                "organization": "ThermoGuard",
                "derived_status": "ACTIVE",
                "status_override": False,
                "status": "active",
                "risk_level": "LOW",
                "device_type": "wall_switch",
                "location": "Room 1",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        fake.seed(
            "inspections",
            "1",
            {
                "id": 1,
                "user_id": uid,
                "panel_id": 1,
                "device_id": 1,  # device-linked → the report is org-scoped to ThermoGuard
                "status": "completed",
                "inspection_code": "TG-000001",
                "mode": "switch_first",
                "camera_source": "webcam",
                "software_version": "1.0",
                "model_version": "cv-switch",
                "started_at": now.isoformat(),
                "ended_at": now.isoformat(),
                "risk_score": 10.0,
                "component_count": 1,
                "frames_processed": 5,
                "duration_seconds": 30,
                "thermal_source": "mlx90640",
                "thermal_simulated": False,
                "notes": None,
            },
        )
        fake.seed("detections", "1", {"id": 1, "inspection_id": 1, "label": "wall_switch", "confidence": 0.99, "bbox": "[1,2,3,4]", "temperature": 42.0, "health": "warning", "frame_index": 5})
        fake.seed("faults", "1", {"id": 1, "inspection_id": 1, "component_label": "wall_switch", "fault_type": "thermal_anomaly", "severity": "high", "confidence": 0.9, "temperature": 42.0, "message": "Localized thermal anomaly", "recommendation": "Professional inspection recommended"})

        from backend.firebase.engine import FirestoreSession
        from backend.services.report_service import ReportService

        report = ReportService(FirestoreSession(fake)).generate_for_inspection(1)
        assert report.file_path.startswith("reports/")  # remote blob path, not a filesystem path
        assert bucket.blob(report.file_path).exists()

        # listing exposes safe metadata only — never the filesystem path
        resp = client.get("/api/v1/reports", headers=headers)
        assert resp.status_code == 200, resp.text
        item = resp.json()[0]
        assert item["file_available"] is True
        assert "file_path" not in item
        assert item["file_name"].endswith(".pdf")

        # authorized download streams the PDF bytes
        resp = client.get(f"/api/v1/reports/{report.id}/download", headers=headers)
        assert resp.status_code == 200
        assert resp.content.startswith(b"%PDF")

        # a foreign-organization user gets 404 on the same report
        resp2 = client.post("/api/v1/auth/firebase", json={"id_token": FAKE_TOKEN_FOREIGN})
        foreign_token = resp2.json()["access_token"]
        update_profile("u-foreign", role="technician", organization="OtherCo")
        foreign_headers = {"Authorization": f"Bearer {foreign_token}"}
        assert client.get(f"/api/v1/reports/{report.id}/download", headers=foreign_headers).status_code == 404
