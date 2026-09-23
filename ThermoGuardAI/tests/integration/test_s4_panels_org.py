"""S4 integration tests — panel/component ownership and report file security.

Ownership chain added in S4: Organization → Panel → Component.

  * Panels created after the upgrade carry their creator's organization;
    legacy panels (NULL org — e.g. the seeded PANEL-MAIN) remain visible to
    every authenticated user.
  * Cross-organization access returns 404 so existence is never revealed.
  * The S2.1 gap (maintenance created with only a component_id bypassing org
    isolation) is closed via Component → Panel ownership.
  * Report API responses expose only safe metadata — never file_path.
"""
from __future__ import annotations

import uuid

from backend.core.security import hash_password
from backend.db.session import SessionLocal
from backend.models.component import Component
from backend.models.device import Device
from backend.models.inspection import Inspection
from backend.models.panel import Panel
from backend.models.user import User

PASSWORD = "password123"


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _add_user(organization: str, role: str = "admin") -> str:
    db = SessionLocal()
    try:
        user = User(
            username=_unique(f"user_{organization.lower()}"),
            email=f"{_unique(organization.lower())}@test.local",
            full_name=organization,
            hashed_password=hash_password(PASSWORD),
            role=role,
            organization=organization,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.username
    finally:
        db.close()


def _add_org_panel(organization: str) -> tuple[int, int]:
    """Create an org-scoped panel + component; returns (panel_id, component_id)."""
    db = SessionLocal()
    try:
        panel = Panel(
            name=f"{organization} Panel",
            code=f"PANEL-{organization.upper()}-{uuid.uuid4().hex[:4]}",
            location="Lab",
            organization=organization,
        )
        db.add(panel)
        db.flush()
        component = Component(
            panel_id=panel.id,
            label="Breaker S4",
            component_type="circuit_breaker",
            code="B-S4",
        )
        db.add(component)
        db.commit()
        return panel.id, component.id
    finally:
        db.close()


def _login(client, username: str) -> dict[str, str]:  # noqa: ANN001
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Panel organization ownership
# ---------------------------------------------------------------------------
def test_panel_list_and_detail_require_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/panels").status_code == 401
    assert client.get("/api/v1/panels/1").status_code == 401


def test_org_scoped_panel_hidden_from_other_org(client) -> None:  # noqa: ANN001
    user_a = _add_user("OrgPanA")
    user_b = _add_user("OrgPanB")
    panel_a, _ = _add_org_panel("OrgPanA")
    headers_a = _login(client, user_a)
    headers_b = _login(client, user_b)

    # owner sees it in the list and by direct id
    own_list = client.get("/api/v1/panels", headers=headers_a).json()
    assert any(p["id"] == panel_a for p in own_list)
    assert client.get(f"/api/v1/panels/{panel_a}", headers=headers_a).status_code == 200

    # other org: hidden from the list, 404 on direct id
    other_list = client.get("/api/v1/panels", headers=headers_b).json()
    assert not any(p["id"] == panel_a for p in other_list)
    assert client.get(f"/api/v1/panels/{panel_a}", headers=headers_b).status_code == 404


def test_legacy_panel_stays_visible_to_everyone(client, auth_headers) -> None:  # noqa: ANN001
    user_b = _add_user("OrgPanLegacy")
    headers_b = _login(client, user_b)
    # the seeded PANEL-MAIN is a legacy NULL-org panel
    listing = client.get("/api/v1/panels", headers=headers_b).json()
    assert any(p["code"] == "PANEL-MAIN" for p in listing)
    for p in listing:
        if p["code"] == "PANEL-MAIN":
            assert client.get(f"/api/v1/panels/{p['id']}", headers=headers_b).status_code == 200


def test_created_panel_inherits_creator_organization(client, auth_headers) -> None:  # noqa: ANN001
    created = client.post(
        "/api/v1/panels",
        json={"name": "Owner Panel", "code": f"PANEL-OWNER-{uuid.uuid4().hex[:4]}"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    # the seeded admin belongs to ThermoGuard
    assert created.json()["organization"] == "ThermoGuard"


def test_add_component_to_foreign_panel_rejected(client) -> None:  # noqa: ANN001
    user_b = _add_user("OrgCompB")
    panel_a, _ = _add_org_panel("OrgCompA")
    headers_b = _login(client, user_b)

    resp = client.post(
        f"/api/v1/panels/{panel_a}/components",
        json={"label": "Sneaky Breaker", "component_type": "circuit_breaker"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Maintenance component ownership (S2.1 gap closed)
# ---------------------------------------------------------------------------
def test_maintenance_with_foreign_component_rejected(client) -> None:  # noqa: ANN001
    user_a = _add_user("OrgMtA")
    user_b = _add_user("OrgMtB")
    _, component_a = _add_org_panel("OrgMtA")
    headers_b = _login(client, user_b)

    # S2.1 gap: a record created with ONLY a component_id must respect org isolation
    resp = client.post(
        "/api/v1/maintenance",
        json={"component_id": component_a, "priority": "high", "fault_type": "overheating"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text

    # the owner can create it
    headers_a = _login(client, user_a)
    ok = client.post(
        "/api/v1/maintenance",
        json={"component_id": component_a, "priority": "high", "fault_type": "overheating"},
        headers=headers_a,
    )
    assert ok.status_code == 201, ok.text


def test_maintenance_with_legacy_component_allowed(client, auth_headers) -> None:  # noqa: ANN001
    """Components on legacy NULL-org panels stay usable by every organization."""
    user_b = _add_user("OrgMtLegacy")
    headers_b = _login(client, user_b)
    db = SessionLocal()
    try:
        legacy = db.query(Component).join(Panel, Component.panel_id == Panel.id).filter(Panel.organization.is_(None)).first()
        assert legacy, "seeded PANEL-MAIN component expected"
        legacy_id = legacy.id
    finally:
        db.close()
    ok = client.post(
        "/api/v1/maintenance",
        json={"component_id": legacy_id, "priority": "low", "fault_type": "overheating"},
        headers=headers_b,
    )
    assert ok.status_code == 201, ok.text


# ---------------------------------------------------------------------------
# Asset lifecycle org scoping
# ---------------------------------------------------------------------------
def test_asset_panel_and_component_org_scoped(client) -> None:  # noqa: ANN001
    user_a = _add_user("OrgAssetA")
    user_b = _add_user("OrgAssetB")
    panel_a, component_a = _add_org_panel("OrgAssetA")
    headers_a = _login(client, user_a)
    headers_b = _login(client, user_b)

    assert client.get(f"/api/v1/assets/panels/{panel_a}", headers=headers_a).status_code == 200
    assert client.get(f"/api/v1/assets/panels/{panel_a}", headers=headers_b).status_code == 404
    assert client.get(f"/api/v1/assets/components/{component_a}", headers=headers_a).status_code == 200
    assert client.get(f"/api/v1/assets/components/{component_a}", headers=headers_b).status_code == 404
    assert client.post(f"/api/v1/assets/components/{component_a}/replace", headers=headers_b).status_code == 404
    assert client.post(f"/api/v1/assets/components/{component_a}/update", json={"retired": True}, headers=headers_b).status_code == 404


def test_asset_overview_excludes_foreign_org_panels(client) -> None:  # noqa: ANN001
    user_b = _add_user("OrgOvB")
    panel_a, _ = _add_org_panel("OrgOvA")
    headers_b = _login(client, user_b)

    overview = client.get("/api/v1/assets/overview", headers=headers_b).json()
    assert not any(p["id"] == panel_a for p in overview["panels"])
    # legacy panel still aggregated for every org
    assert any(p["code"] == "PANEL-MAIN" for p in overview["panels"])


# ---------------------------------------------------------------------------
# Inspection management org scoping (direct-ID attempts)
# ---------------------------------------------------------------------------
def _add_org_inspection(organization: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.organization == organization).first()
        device = Device(name=f"{organization} Device", device_type="wall_switch", organization=organization, status="ACTIVE")
        db.add(device)
        db.flush()
        from datetime import UTC, datetime

        inspection = Inspection(
            user_id=user.id if user else None,
            device_id=device.id,
            mode="quick",
            camera_source="test",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(inspection)
        db.commit()
        return inspection.id
    finally:
        db.close()


def test_inspection_mutations_org_scoped(client) -> None:  # noqa: ANN001
    user_b = _add_user("OrgInsB")
    inspection_a = _add_org_inspection("OrgInsA")
    headers_b = _login(client, user_b)

    for path in (
        f"/api/v1/inspections/{inspection_a}/archive",
        f"/api/v1/inspections/{inspection_a}/stop",
        f"/api/v1/inspections/{inspection_a}/abort",
    ):
        assert client.post(path, json={}, headers=headers_b).status_code == 404, path
    assert client.delete(f"/api/v1/inspections/{inspection_a}", headers=headers_b).status_code == 404

    # foreign inspection still exists and stays running
    db = SessionLocal()
    try:
        assert db.get(Inspection, inspection_a).status == "running"
    finally:
        db.close()


def test_analytics_predictive_org_scoped(client) -> None:  # noqa: ANN001
    user_a = _add_user("OrgPredA")
    user_b = _add_user("OrgPredB")
    panel_a, _ = _add_org_panel("OrgPredA")
    headers_b = _login(client, user_b)
    assert client.get("/api/v1/analytics/predictive", params={"panel_id": panel_a}, headers=headers_b).status_code == 404
    headers_a = _login(client, user_a)
    assert client.get("/api/v1/analytics/predictive", params={"panel_id": panel_a}, headers=headers_a).status_code == 200


# ---------------------------------------------------------------------------
# Report file security: no internal paths in API responses
# ---------------------------------------------------------------------------
def test_report_response_never_exposes_file_path(client, auth_headers) -> None:  # noqa: ANN001
    start = client.post("/api/v1/inspections", json={"mode": "manual", "camera_source": "test"}, headers=auth_headers)
    inspection_id = start.json()["id"]
    report = client.post(f"/api/v1/reports/inspections/{inspection_id}", json={}, headers=auth_headers)
    assert report.status_code == 201, report.text
    body = report.json()

    assert "file_path" not in body, "server filesystem path must never leave the API"
    assert body.get("file_name", "").endswith(".pdf"), "safe display name expected"

    listing = client.get("/api/v1/reports", headers=auth_headers).json()
    assert all("file_path" not in r for r in listing)

    single = client.get(f"/api/v1/reports/{body['id']}", headers=auth_headers).json()
    assert "file_path" not in single

    # download still works through the authorized endpoint
    download = client.get(f"/api/v1/reports/{body['id']}/download", headers=auth_headers)
    assert download.status_code == 200
    assert download.content[:4] == b"%PDF"
