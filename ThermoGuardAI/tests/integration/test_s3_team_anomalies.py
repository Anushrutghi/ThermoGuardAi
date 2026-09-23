"""S3 tests: global anomaly center + team/users endpoints, with org isolation.

    GET /anomalies  — org-scoped real fault aggregation
    GET /users      — admin-only, org-scoped team list (NULL matches NULL)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.core.security import hash_password
from backend.db.session import SessionLocal
from backend.models.device import Device
from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.user import User

PASSWORD = "password123"


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _add_org_with_anomaly(organization: str, fault_type: str = "thermal_anomaly", severity: str = "high") -> tuple[str, int, int]:
    """Create an org user, device, completed inspection and a real fault.

    Returns (username, device_id, fault_id).
    """
    db = SessionLocal()
    try:
        user = User(
            username=_unique(f"orguser_{organization.lower()}"),
            email=f"{_unique(organization.lower())}@test.local",
            full_name=organization,
            hashed_password=hash_password(PASSWORD),
            role="admin",
            organization=organization,
            is_active=True,
        )
        db.add(user)
        db.flush()
        device = Device(name=f"{organization} Device", device_type="switch", organization=organization, status="ACTIVE")
        db.add(device)
        db.flush()
        inspection = Inspection(
            user_id=user.id,
            device_id=device.id,
            mode="switch_first",
            camera_source="test",
            status="completed",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            risk_score=45.0,
            inspection_code=f"TG-{uuid.uuid4().hex[:10].upper()}",
        )
        db.add(inspection)
        db.flush()
        fault = Fault(
            inspection_id=inspection.id,
            component_label="Switch",
            fault_type=fault_type,
            severity=severity,
            confidence=0.9,
            temperature=54.0,
            message="Thermal anomaly detected",
            recommendation="Professional electrical inspection recommended.",
        )
        db.add(fault)
        db.commit()
        return user.username, device.id, fault.id
    finally:
        db.close()


def _add_orgless_admin() -> str:
    db = SessionLocal()
    try:
        user = User(
            username=_unique("orglessadmin"),
            email=f"{_unique('orglessadmin')}@test.local",
            full_name="Org-less Admin",
            hashed_password=hash_password(PASSWORD),
            role="admin",
            organization=None,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.username
    finally:
        db.close()


def _login(client, username: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Global anomaly center
# ---------------------------------------------------------------------------
def test_anomaly_center_shows_own_org_only(client) -> None:  # noqa: ANN001
    a_user, _, a_fault_id = _add_org_with_anomaly("OrgAnomA")
    b_user, _, b_fault_id = _add_org_with_anomaly("OrgAnomB")

    headers_a = _login(client, a_user)
    listing = client.get("/api/v1/anomalies", headers=headers_a)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    ids = {a["fault_id"] for a in body["items"]}
    assert a_fault_id in ids, "own organization's anomaly must be listed"
    assert b_fault_id not in ids, "another organization's anomaly must never be listed"
    item = next(a for a in body["items"] if a["fault_id"] == a_fault_id)
    assert item["device_name"] == "OrgAnomA Device"
    assert item["kind"]  # human-readable anomaly label
    assert item["occurred_at"]

    # cross-org device filter → 404 (no existence leak)
    _, b_device_id, _ = _add_org_with_anomaly("OrgAnomC")
    foreign = client.get(f"/api/v1/anomalies?device_id={b_device_id}", headers=headers_a)
    assert foreign.status_code == 404, foreign.text


def test_anomaly_center_filters(client) -> None:  # noqa: ANN001
    a_user, a_device_id, _ = _add_org_with_anomaly("OrgAnomD", fault_type="critical_thermal_anomaly", severity="critical")
    headers_a = _login(client, a_user)

    by_device = client.get(f"/api/v1/anomalies?device_id={a_device_id}", headers=headers_a).json()
    assert by_device["total"] == 1

    by_sev = client.get("/api/v1/anomalies?severity=critical", headers=headers_a).json()
    assert by_sev["total"] == 1

    empty = client.get("/api/v1/anomalies?severity=low", headers=headers_a).json()
    assert empty["total"] == 0
    assert empty["items"] == []


def test_anomaly_center_requires_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/anomalies").status_code == 401


# ---------------------------------------------------------------------------
# Team / users
# ---------------------------------------------------------------------------
def test_users_org_scoped_and_admin_only(client) -> None:  # noqa: ANN001
    a_admin, _, _ = _add_org_with_anomaly("OrgTeamA")
    b_admin, _, _ = _add_org_with_anomaly("OrgTeamB")
    a_headers = _login(client, a_admin)

    # Org A sees its own users only — never Org B's
    listing = client.get("/api/v1/users", headers=a_headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] >= 1
    usernames = {u["username"] for u in body["items"]}
    assert a_admin in usernames
    assert b_admin not in usernames, "must never expose another organization's users"

    # role/status fields present
    row = body["items"][0]
    for field in ("username", "email", "full_name", "role", "is_active", "organization"):
        assert field in row, field

    # non-admin cannot list users
    db = SessionLocal()
    try:
        tech = User(
            username=_unique("tech"),
            email=f"{_unique('tech')}@test.local",
            full_name="Tech",
            hashed_password=hash_password(PASSWORD),
            role="technician",
            organization="OrgTeamA",
            is_active=True,
        )
        db.add(tech)
        db.commit()
        tech_name = tech.username
    finally:
        db.close()
    forbidden = client.get("/api/v1/users", headers=_login(client, tech_name))
    assert forbidden.status_code == 403, forbidden.text


def test_users_orgless_matches_null(client) -> None:  # noqa: ANN001
    admin = _add_orgless_admin()
    headers = _login(client, admin)
    listing = client.get("/api/v1/users", headers=headers)
    assert listing.status_code == 200
    assert all(u["organization"] is None for u in listing.json()["items"]), (
        "org-less admin must only see org-less users"
    )
