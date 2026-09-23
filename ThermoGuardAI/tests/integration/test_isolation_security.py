"""S2.1 security tests: cross-organization report & maintenance isolation.

Ownership chains under test:

    Organization → Device → Inspection → Report
    Organization → Device → Inspection → Maintenance

Every cross-organization access attempt (list, lookup, download, create,
update, delete) must return a 404 not-found response so a direct-ID guess
never reveals that another organization's record exists. Organization-less
users may only see unowned (legacy) records — never another org's device-
linked records.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.core.security import hash_password
from backend.db.session import SessionLocal
from backend.models.device import Device
from backend.models.inspection import Inspection
from backend.models.maintenance import MaintenanceRecord
from backend.models.report import Report
from backend.models.user import User

PASSWORD = "password123"


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _add_org_data(organization: str) -> tuple[int, int, int, int, int]:
    """Create user + device + inspection + report + maintenance for one org.

    Returns (username, device_id, inspection_id, report_id, maintenance_id).
    The report gets a real file on disk so download tests exercise the
    authorization gate (404 from org isolation) rather than a missing file.
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
        device = Device(
            name=f"{organization} Switch",
            device_type="switch",
            organization=organization,
            status="ACTIVE",
        )
        db.add(device)
        db.flush()
        inspection = Inspection(
            user_id=user.id,
            device_id=device.id,
            mode="quick",
            camera_source="test",
            status="completed",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            risk_score=25.0,
        )
        db.add(inspection)
        db.flush()
        report_file = Path(tempfile.gettempdir()) / f"tg_{organization.lower()}_{uuid.uuid4().hex[:6]}.pdf"
        report_file.write_bytes(b"%PDF-1.4\nS2.1-test-report\n")
        report = Report(
            inspection_id=inspection.id,
            title=f"{organization} inspection report",
            file_path=str(report_file),
            risk_score=25.0,
            generated_at=datetime.now(UTC),
        )
        db.add(report)
        maintenance = MaintenanceRecord(
            code=f"MT-{organization.upper()}-{uuid.uuid4().hex[:4].upper()}",
            inspection_id=inspection.id,
            priority="high",
            status="pending",
        )
        db.add(maintenance)
        db.commit()
        return user.username, device.id, inspection.id, report.id, maintenance.id
    finally:
        db.close()


def _add_null_org_device_data() -> tuple[int, int]:
    """Device-linked records where the device has organization=NULL.

    NULL matches NULL: these are visible to organization-less users only, and
    must be as invisible to org'd users in the detail paths as they are in the
    list paths.
    """
    db = SessionLocal()
    try:
        device = Device(name="Unowned Switch", device_type="switch", organization=None, status="ACTIVE")
        db.add(device)
        db.flush()
        inspection = Inspection(
            user_id=None,
            device_id=device.id,
            mode="quick",
            camera_source="test",
            status="completed",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            risk_score=10.0,
        )
        db.add(inspection)
        db.flush()
        report = Report(
            inspection_id=inspection.id,
            title="NULL-org device report",
            file_path=str(Path(tempfile.gettempdir()) / f"tg_nullorg_{uuid.uuid4().hex[:6]}.pdf"),
            risk_score=10.0,
            generated_at=datetime.now(UTC),
        )
        db.add(report)
        maintenance = MaintenanceRecord(
            code=f"MT-NULLORG-{uuid.uuid4().hex[:4].upper()}",
            inspection_id=inspection.id,
            priority="medium",
            status="pending",
        )
        db.add(maintenance)
        db.commit()
        return report.id, maintenance.id
    finally:
        db.close()


def _add_legacy_records() -> tuple[int, int]:
    """Unowned legacy records (no inspection link) — visible to every user."""
    db = SessionLocal()
    try:
        report = Report(
            inspection_id=None,
            title="Legacy unowned report",
            file_path=str(Path(tempfile.gettempdir()) / f"tg_legacy_{uuid.uuid4().hex[:6]}.pdf"),
            risk_score=0.0,
            generated_at=datetime.now(UTC),
        )
        db.add(report)
        maintenance = MaintenanceRecord(
            code=f"MT-LEGACY-{uuid.uuid4().hex[:6].upper()}",
            inspection_id=None,
            priority="low",
            status="pending",
        )
        db.add(maintenance)
        db.commit()
        return report.id, maintenance.id
    finally:
        db.close()


def _add_orgless_technician() -> str:
    """An active technician with organization=NULL (self-registered style)."""
    db = SessionLocal()
    try:
        user = User(
            username=_unique("orgless"),
            email=f"{_unique('orgless')}@test.local",
            full_name="Org-less tech",
            hashed_password=hash_password(PASSWORD),
            role="technician",
            organization=None,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.username
    finally:
        db.close()


def _login(client, username: str) -> tuple[dict[str, str], str]:
    """Login a user; returns (auth headers, raw token)."""
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, token


# ---------------------------------------------------------------------------
# Report isolation
# ---------------------------------------------------------------------------
def test_cross_org_report_list(client) -> None:  # noqa: ANN001
    _, _, _, a_report_id, _ = _add_org_data("OrgAlpha")
    b_user, _, _, b_report_id, _ = _add_org_data("OrgBeta")
    headers_b, _ = _login(client, b_user)

    listing = client.get("/api/v1/reports", headers=headers_b)
    assert listing.status_code == 200, listing.text
    ids = {r["id"] for r in listing.json()}
    assert b_report_id in ids, "own organization's report must be listed"
    assert a_report_id not in ids, "another organization's report must never be listed"


def test_cross_org_report_lookup(client) -> None:  # noqa: ANN001
    a_user, _, _, a_report_id, _ = _add_org_data("OrgAlpha")
    b_user, _, _, b_report_id, _ = _add_org_data("OrgBeta")
    headers_a, _ = _login(client, a_user)
    headers_b, _ = _login(client, b_user)

    # own report → 200
    own = client.get(f"/api/v1/reports/{a_report_id}", headers=headers_a)
    assert own.status_code == 200, own.text

    # other org's report → 404 (not found, not 403 — existence is not revealed)
    foreign = client.get(f"/api/v1/reports/{b_report_id}", headers=headers_a)
    assert foreign.status_code == 404, foreign.text


def test_cross_org_report_download(client) -> None:  # noqa: ANN001
    a_user, _, _, a_report_id, _ = _add_org_data("OrgAlpha")
    b_user, _, _, b_report_id, _ = _add_org_data("OrgBeta")
    headers_a, token_a = _login(client, a_user)
    headers_b, token_b = _login(client, b_user)

    # own download (file exists) → 200
    own = client.get(f"/api/v1/reports/{a_report_id}/download", headers=headers_a)
    assert own.status_code == 200, own.text
    assert own.headers["content-type"] == "application/pdf"

    # other org's download → 404 even though the file exists on disk
    foreign = client.get(f"/api/v1/reports/{b_report_id}/download", headers=headers_a)
    assert foreign.status_code == 404, foreign.text

    # ?token= query-param auth must be equally org-scoped
    via_token = client.get(
        f"/api/v1/reports/{b_report_id}/download", params={"token": token_a}
    )
    assert via_token.status_code == 404, via_token.text


def test_cross_org_report_creation(client) -> None:  # noqa: ANN001
    a_user, _, _, _, _ = _add_org_data("OrgAlpha")
    b_user, _, b_inspection_id, _, _ = _add_org_data("OrgBeta")
    headers_a, _ = _login(client, a_user)

    # generating a report from another org's inspection must be impossible
    forbidden = client.post(
        f"/api/v1/reports/inspections/{b_inspection_id}", json={}, headers=headers_a
    )
    assert forbidden.status_code == 404, forbidden.text


# ---------------------------------------------------------------------------
# Maintenance isolation
# ---------------------------------------------------------------------------
def test_cross_org_maintenance_list(client) -> None:  # noqa: ANN001
    _, _, _, _, a_maint_id = _add_org_data("OrgAlpha")
    b_user, _, _, _, b_maint_id = _add_org_data("OrgBeta")
    headers_b, _ = _login(client, b_user)

    listing = client.get("/api/v1/maintenance", headers=headers_b)
    assert listing.status_code == 200, listing.text
    ids = {r["id"] for r in listing.json()["items"]}
    assert b_maint_id in ids, "own organization's maintenance must be listed"
    assert a_maint_id not in ids, "another organization's maintenance must never be listed"


def test_cross_org_maintenance_lookup(client) -> None:  # noqa: ANN001
    a_user, _, _, _, a_maint_id = _add_org_data("OrgAlpha")
    b_user, _, _, _, b_maint_id = _add_org_data("OrgBeta")
    headers_a, _ = _login(client, a_user)

    own = client.get(f"/api/v1/maintenance/{a_maint_id}", headers=headers_a)
    assert own.status_code == 200, own.text

    foreign = client.get(f"/api/v1/maintenance/{b_maint_id}", headers=headers_a)
    assert foreign.status_code == 404, foreign.text


def test_cross_org_maintenance_update(client) -> None:  # noqa: ANN001
    a_user, _, _, _, a_maint_id = _add_org_data("OrgAlpha")
    b_user, _, _, _, b_maint_id = _add_org_data("OrgBeta")
    headers_a, _ = _login(client, a_user)

    own = client.post(
        f"/api/v1/maintenance/{a_maint_id}/update",
        json={"status": "in_progress"},
        headers=headers_a,
    )
    assert own.status_code == 200, own.text
    assert own.json()["status"] == "in_progress"

    foreign = client.post(
        f"/api/v1/maintenance/{b_maint_id}/update",
        json={"status": "completed"},
        headers=headers_a,
    )
    assert foreign.status_code == 404, foreign.text

    # the foreign record is untouched
    db = SessionLocal()
    try:
        from backend.models.maintenance import MaintenanceRecord

        record = db.get(MaintenanceRecord, b_maint_id)
        assert record is not None
        assert record.status == "pending", "foreign record must remain unchanged"
    finally:
        db.close()


def test_cross_org_maintenance_delete(client) -> None:  # noqa: ANN001
    a_user, _, _, _, _ = _add_org_data("OrgAlpha")
    b_user, _, _, _, b_maint_id = _add_org_data("OrgBeta")
    headers_a, _ = _login(client, a_user)

    foreign = client.delete(f"/api/v1/maintenance/{b_maint_id}", headers=headers_a)
    assert foreign.status_code == 404, foreign.text

    db = SessionLocal()
    try:
        from backend.models.maintenance import MaintenanceRecord

        assert db.get(MaintenanceRecord, b_maint_id) is not None, "foreign record must survive"
    finally:
        db.close()


def test_maintenance_creation_against_other_org_device(client) -> None:  # noqa: ANN001
    a_user, _, _, _, _ = _add_org_data("OrgAlpha")
    b_user, _, b_inspection_id, _, _ = _add_org_data("OrgBeta")
    headers_a, _ = _login(client, a_user)

    # Org A must not be able to open a maintenance order on Org B's inspection
    forbidden = client.post(
        "/api/v1/maintenance",
        json={"inspection_id": b_inspection_id, "priority": "high", "fault_type": "overheating"},
        headers=headers_a,
    )
    assert forbidden.status_code == 404, forbidden.text

    # Org B can open one on its own inspection
    allowed = client.post(
        "/api/v1/maintenance",
        json={"inspection_id": b_inspection_id, "priority": "high", "fault_type": "overheating"},
        headers=_login(client, b_user)[0],
    )
    assert allowed.status_code == 201, allowed.text


# ---------------------------------------------------------------------------
# Direct-ID authorization bypass attempts
# ---------------------------------------------------------------------------
def test_direct_id_authorization_bypass_attempts(client) -> None:  # noqa: ANN001
    a_user, _, _, a_report_id, a_maint_id = _add_org_data("OrgAlpha")
    b_user, _, _, b_report_id, b_maint_id = _add_org_data("OrgBeta")
    headers_a, token_a = _login(client, a_user)
    _, token_b = _login(client, b_user)

    # cross-org token + cross-org ID through EVERY read path → 404
    assert client.get(f"/api/v1/reports/{b_report_id}", headers=headers_a).status_code == 404
    assert (
        client.get(f"/api/v1/reports/{b_report_id}/download", params={"token": token_a}).status_code == 404
    )
    assert client.get(f"/api/v1/maintenance/{b_maint_id}", headers=headers_a).status_code == 404

    # same-org token + same-org ID still works (guard isn't a blanket 404)
    assert client.get(f"/api/v1/reports/{a_report_id}", headers=_login(client, a_user)[0]).status_code == 200
    assert client.get(f"/api/v1/maintenance/{a_maint_id}", headers=headers_a).status_code == 200

    # unauthenticated download (bare URL with no token) → 401
    assert client.get(f"/api/v1/reports/{a_report_id}/download").status_code == 401

    # forged/cross-org token cannot access own-org data via ?token= either
    assert (
        client.get(f"/api/v1/reports/{a_report_id}/download", params={"token": token_b}).status_code == 404
    )


# ---------------------------------------------------------------------------
# Organization-less users
# ---------------------------------------------------------------------------
def test_organization_less_users(client) -> None:  # noqa: ANN001
    """Org-less users see only unowned legacy records — never org-linked ones."""
    org_user, _, _, org_report_id, org_maint_id = _add_org_data("OrgGamma")
    legacy_report_id, legacy_maint_id = _add_legacy_records()
    orgless = _add_orgless_technician()
    headers, _ = _login(client, orgless)
    _, _ = _login(client, org_user)

    reports = client.get("/api/v1/reports", headers=headers)
    assert reports.status_code == 200, reports.text
    report_ids = {r["id"] for r in reports.json()}
    assert legacy_report_id in report_ids, "org-less user must see unowned legacy reports"
    assert org_report_id not in report_ids, "org-less user must not see org-linked reports"

    maintenance = client.get("/api/v1/maintenance", headers=headers).json()["items"]
    maint_ids = {r["id"] for r in maintenance}
    assert legacy_maint_id in maint_ids, "org-less user must see unowned legacy maintenance"
    assert org_maint_id not in maint_ids, "org-less user must not see org-linked maintenance"

    # direct lookups of org-linked records also 404 for org-less users
    assert client.get(f"/api/v1/reports/{org_report_id}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/maintenance/{org_maint_id}", headers=headers).status_code == 404


def test_null_org_device_isolation(client) -> None:  # noqa: ANN001
    """NULL-org devices are owned by no organization: org-less users see them,
    org'd users do not — and list/detail views must agree."""
    null_report_id, null_maint_id = _add_null_org_device_data()
    org_user, _, _, org_report_id, org_maint_id = _add_org_data("OrgDelta")
    orgless = _add_orgless_technician()

    headers_org, _ = _login(client, org_user)
    headers_orgless, _ = _login(client, orgless)

    # org'd user: hidden from lists AND 404 on direct access (no leak)
    org_reports = {r["id"] for r in client.get("/api/v1/reports", headers=headers_org).json()}
    assert null_report_id not in org_reports
    assert org_report_id in org_reports
    org_maint = {r["id"] for r in client.get("/api/v1/maintenance", headers=headers_org).json()["items"]}
    assert null_maint_id not in org_maint
    assert client.get(f"/api/v1/reports/{null_report_id}", headers=headers_org).status_code == 404
    assert client.get(f"/api/v1/maintenance/{null_maint_id}", headers=headers_org).status_code == 404

    # org-less user: NULL matches NULL — visible in lists and via direct access
    orgless_reports = {r["id"] for r in client.get("/api/v1/reports", headers=headers_orgless).json()}
    assert null_report_id in orgless_reports
    assert org_report_id not in orgless_reports
    orgless_maint = {r["id"] for r in client.get("/api/v1/maintenance", headers=headers_orgless).json()["items"]}
    assert null_maint_id in orgless_maint
    assert client.get(f"/api/v1/reports/{null_report_id}", headers=headers_orgless).status_code == 200
    assert client.get(f"/api/v1/maintenance/{null_maint_id}", headers=headers_orgless).status_code == 200
