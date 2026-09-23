"""S4 tests — health endpoints, thermal status endpoint, camera control auth,
alarm org scoping.
"""
from __future__ import annotations

import uuid

from backend.core.security import hash_password
from backend.db.session import SessionLocal
from backend.models.alarm import Alarm
from backend.models.device import Device
from backend.models.inspection import Inspection
from backend.models.user import User

PASSWORD = "password123"


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _login(client, username: str) -> dict[str, str]:  # noqa: ANN001
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _add_org_user(organization: str) -> str:
    db = SessionLocal()
    try:
        user = User(
            username=_unique(f"hl_{organization.lower()}"),
            email=f"{_unique(organization.lower())}@test.local",
            full_name=organization,
            hashed_password=hash_password(PASSWORD),
            role="admin",
            organization=organization,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.username
    finally:
        db.close()


def _add_org_device(organization: str) -> int:
    db = SessionLocal()
    try:
        device = Device(name=f"{organization} Switch", device_type="wall_switch", organization=organization, status="ACTIVE")
        db.add(device)
        db.flush()
        from datetime import UTC, datetime

        inspection = Inspection(
            device_id=device.id,
            mode="quick",
            camera_source="test",
            status="completed",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )
        db.add(inspection)
        db.flush()
        db.add(
            Alarm(
                inspection_id=inspection.id,
                severity="high",
                message=f"{organization} alarm",
                source="test",
            )
        )
        db.commit()
        return device.id
    finally:
        db.close()


def _org_alarm_id(organization: str) -> int:
    """The alarm created for an org's device-linked inspection."""
    db = SessionLocal()
    try:
        alarm = (
            db.query(Alarm)
            .join(Inspection, Alarm.inspection_id == Inspection.id)
            .join(Device, Inspection.device_id == Device.id)
            .filter(Device.organization == organization, Alarm.message == f"{organization} alarm")
            .first()
        )
        assert alarm is not None
        return alarm.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------
def test_health_live_and_ready(client) -> None:  # noqa: ANN001
    live = client.get("/health/live")
    assert live.status_code == 200 and live.json()["status"] == "ok"

    ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["database"] == "ok"


def test_health_aggregate_reports_thermal_honestly(client) -> None:  # noqa: ANN001
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database_ok"] is True
    # DEMO mode: the thermal subsystem reports simulated=true, never implied real
    assert "thermal" in body
    assert "thermal_simulated" in body
    assert "thermal_lifecycle" in body
    assert body["thermal_lifecycle"] in (
        "CONNECTING", "CONNECTED", "CALIBRATING", "STABILIZING", "BASELINE",
        "READY", "SCANNING", "DISCONNECTED", "ERROR",
    )


# ---------------------------------------------------------------------------
# Thermal status endpoint (authoritative REAL vs DEMO)
# ---------------------------------------------------------------------------
def test_thermal_status_endpoint(client, auth_headers) -> None:  # noqa: ANN001
    resp = client.get("/api/v1/thermal/status", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # the backend decides REAL vs DEMO — tests run in simulator mode
    assert body["simulated"] is True
    assert body["hardware"] == "DEMO / SIMULATED THERMAL"
    assert body["source"] == "simulator"
    assert body["metadata"]["simulated"] is True
    assert body["metadata"]["emissivity"] is None, "emissivity must not be invented"
    assert body["disclaimer"]


def test_thermal_status_requires_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/thermal/status").status_code == 401


# ---------------------------------------------------------------------------
# Camera control endpoints require auth
# ---------------------------------------------------------------------------
def test_camera_control_requires_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/cameras").status_code == 401
    assert client.post("/api/v1/cameras/switch", json={"kind": "webcam"}).status_code == 401
    assert client.post("/api/v1/cameras/switch/phone").status_code == 401
    assert client.post("/api/v1/cameras/stop").status_code == 401


def test_camera_control_allowed_for_authenticated(client, auth_headers) -> None:  # noqa: ANN001
    """Authenticated camera control works (with a stubbed manager — never
    touches real webcam hardware in tests)."""
    import numpy as np

    from camera.capture_manager import CaptureManager

    class _FakeSource:  # noqa: D401
        class _Info:
            id = "fake-cam"
            name = "Fake Camera"
            kind = "webcam"

        info = _Info()
        frame = np.full((120, 160, 3), 90, dtype=np.uint8)

        def start(self) -> None:
            pass

        def read(self) -> np.ndarray:
            return self.frame

        def stop(self) -> None:
            pass

    manager = CaptureManager(target_fps=30.0)
    manager.switch(_FakeSource())
    from backend.services import camera_service

    camera_service._manager = manager  # noqa: SLF001
    try:
        listing = client.get("/api/v1/cameras", headers=auth_headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["active_id"] == "fake-cam"
        stop = client.post("/api/v1/cameras/stop", headers=auth_headers)
        assert stop.status_code == 200
    finally:
        camera_service._manager = None  # noqa: SLF001


# ---------------------------------------------------------------------------
# Alarm org scoping
# ---------------------------------------------------------------------------
def test_alarms_org_scoped(client) -> None:  # noqa: ANN001
    """Each org sees its own device-linked alarms and never the other org's.
    (Legacy device-less alarms remain shared by design — the established
    legacy-data policy.)"""
    user_a = _add_org_user("OrgAlarmA")
    user_b = _add_org_user("OrgAlarmB")
    _add_org_device("OrgAlarmA")
    _add_org_device("OrgAlarmB")
    alarm_a = _org_alarm_id("OrgAlarmA")
    alarm_b = _org_alarm_id("OrgAlarmB")
    headers_a = _login(client, user_a)
    headers_b = _login(client, user_b)

    list_a = client.get("/api/v1/alarms", headers=headers_a).json()
    list_b = client.get("/api/v1/alarms", headers=headers_b).json()
    ids_a = {a["id"] for a in list_a}
    ids_b = {a["id"] for a in list_b}
    assert alarm_a in ids_a, "own organization's alarm must be listed"
    assert alarm_b in ids_b, "own organization's alarm must be listed"
    assert alarm_a not in ids_b, "another org's alarm must never be listed"
    assert alarm_b not in ids_a, "another org's alarm must never be listed"


def test_alarm_acknowledge_org_scoped(client) -> None:  # noqa: ANN001
    user_a = _add_org_user("OrgAlarmAckA")
    user_b = _add_org_user("OrgAlarmAckB")
    _add_org_device("OrgAlarmAckA")
    _add_org_device("OrgAlarmAckB")
    headers_a = _login(client, user_a)
    headers_b = _login(client, user_b)

    alarm_a = client.get("/api/v1/alarms", headers=headers_a).json()[0]["id"]
    # other org's technician cannot acknowledge it
    assert client.post(f"/api/v1/alarms/{alarm_a}/acknowledge", headers=headers_b).status_code == 404
    # owner can
    assert client.post(f"/api/v1/alarms/{alarm_a}/acknowledge", headers=headers_a).status_code == 200
