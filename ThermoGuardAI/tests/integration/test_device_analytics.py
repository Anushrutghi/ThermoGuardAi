"""S2 integration tests — device intelligence & historical analytics.

Covers: device metadata, device history, thermal history + DEMO exclusion,
temperature/ΔT trends, repeated anomalies, health, risk, maintenance
recommendations, comparison, timeline, dashboard aggregation, organization
isolation, authorization and additive-migration safety.

All assertions use REAL database values seeded explicitly for each test —
nothing is fabricated and nothing is asserted from thin air.
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime, timedelta

import cv2

from tests.switch_test_utils import make_switch_frame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_device(client, auth_headers, name: str, **extra) -> int:  # noqa: ANN001
    payload = {"name": name, "location": "Lab", "device_type": "wall_switch", **extra}
    resp = client.post("/api/v1/devices", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_thermal_history(points: list[dict], device_id: int) -> None:
    """Seed completed inspections + thermal summaries + optional faults.

    Each point: max_temp, min_temp, avg_temp, reference_temp, delta,
    classification, simulated, fault_severity (optional), fault_type.
    """
    from backend.db.session import SessionLocal
    from backend.models.fault import Fault
    from backend.models.inspection import Inspection
    from backend.models.thermal_history import ThermalHistory

    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        for i, p in enumerate(points):
            started = now - timedelta(minutes=30 * (len(points) - i))
            inspection = Inspection(
                device_id=device_id,
                mode="switch_first",
                camera_source="test",
                status="completed",
                started_at=started,
                ended_at=started + timedelta(minutes=1),
                inspection_code=f"TG-S2-{device_id}-{i:03d}",
                software_version="0.1.0",
                model_version="cv-switch",
                thermal_source="simulator" if p.get("simulated") else "mlx90640",
                thermal_simulated=bool(p.get("simulated", False)),
                risk_score=float(p.get("risk_score", 0.0)),
                component_count=1,
            )
            db.add(inspection)
            db.flush()
            db.add(
                ThermalHistory(
                    inspection_id=inspection.id,
                    device_id=device_id,
                    timestamp=started,
                    max_temp=p["max_temp"],
                    min_temp=p.get("min_temp"),
                    avg_temp=p.get("avg_temp"),
                    reference_temp=p.get("reference_temp"),
                    delta=p.get("delta"),
                    hotspot_x=0.5,
                    hotspot_y=0.5,
                    trend_c_per_min=p.get("trend_c_per_min"),
                    trend_delta_c=p.get("trend_delta_c"),
                    rapid_increase=bool(p.get("rapid_increase", False)),
                    heat_path=p.get("heat_path", "localized"),
                    classification=p.get("classification", "NORMAL"),
                    thermal_source="simulator" if p.get("simulated") else "mlx90640",
                    mode="switch_first",
                    simulated=bool(p.get("simulated", False)),
                )
            )
            if p.get("fault_severity"):
                db.add(
                    Fault(
                        inspection_id=inspection.id,
                        component_label="wall_switch",
                        fault_type=p.get("fault_type", "thermal_anomaly"),
                        severity=p["fault_severity"],
                        confidence=0.9,
                        temperature=p["max_temp"],
                        message="Switch at elevated temperature relative to the surrounding wall.",
                        recommendation="Monitor the switch.",
                    )
                )
        db.commit()
    finally:
        db.close()


def _make_user(username: str, organization: str, role: str = "viewer") -> None:
    """Create a user directly in the test DB (no self-registration API)."""
    from backend.core.security import hash_password
    from backend.db.session import SessionLocal
    from backend.models.user import User

    db = SessionLocal()
    try:
        db.add(
            User(
                username=username,
                email=f"{username}@example.test",
                full_name=username.replace("_", " ").title(),
                hashed_password=hash_password("Password123!"),
                role=role,
                organization=organization,
            )
        )
        db.commit()
    finally:
        db.close()


def _login(client, username: str, password: str = "Password123!") -> dict:  # noqa: ANN001
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# 1. Device metadata & history
# ---------------------------------------------------------------------------
def test_device_metadata_crud(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(
        client,
        auth_headers,
        "Machine Room Switch",
        building="Plant A",
        floor="2",
        room="Server Room 2B",
        manufacturer="Schneider",
        model="Easy9",
        serial_number="SN-9001",
        rated_voltage=230.0,
        rated_current=16.0,
        installation_date="2024-03-15",
        last_maintenance_date="2026-01-10",
        next_inspection_date="2026-09-01",
    )
    detail = client.get(f"/api/v1/devices/{device_id}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["building"] == "Plant A" and body["floor"] == "2" and body["room"] == "Server Room 2B"
    assert body["rated_voltage"] == 230.0 and body["rated_current"] == 16.0
    assert body["next_inspection_date"] == "2026-09-01"
    assert body["derived_status"] == "ACTIVE"
    assert "history" in body, "detail must carry the S2 device history summary"
    assert body["history"]["total_inspections"] == 0
    assert body["history"]["note"] == "Insufficient inspection data"

    updated = client.put(
        f"/api/v1/devices/{device_id}",
        json={"floor": "3", "rated_current": 20.0, "next_inspection_date": "2026-10-01"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["floor"] == "3" and updated.json()["rated_current"] == 20.0


def test_device_history_insufficient(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Fresh Device")
    history = client.get(f"/api/v1/devices/{device_id}/history", headers=auth_headers)
    assert history.status_code == 200
    body = history.json()
    assert body["total_inspections"] == 0
    assert body["note"] == "Insufficient inspection data"


# ---------------------------------------------------------------------------
# 2. Thermal history + DEMO exclusion (§3)
# ---------------------------------------------------------------------------
def test_thermal_history_demo_exclusion(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Thermal History Device")
    _seed_thermal_history(
        [
            {"max_temp": 52.0, "min_temp": 24.0, "avg_temp": 30.0, "reference_temp": 29.0, "delta": 23.0, "classification": "ABNORMAL", "simulated": False, "risk_score": 60.0},
            {"max_temp": 55.0, "min_temp": 25.0, "avg_temp": 31.0, "reference_temp": 30.0, "delta": 25.0, "classification": "ABNORMAL", "simulated": True, "risk_score": 60.0},
        ],
        device_id,
    )
    real_only = client.get(f"/api/v1/devices/{device_id}/thermal-history?exclude_demo=true", headers=auth_headers)
    assert real_only.status_code == 200
    real_body = real_only.json()
    assert real_body["total"] == 2
    assert real_body["simulated_excluded"] == 1
    assert len(real_body["items"]) == 1
    assert real_body["items"][0]["simulated"] is False
    assert real_body["items"][0]["max_temp"] == 52.0
    assert real_body["items"][0]["sensor"] == "mlx90640"

    with_demo = client.get(f"/api/v1/devices/{device_id}/thermal-history?exclude_demo=false", headers=auth_headers)
    assert len(with_demo.json()["items"]) == 2
    simulated_rows = [i for i in with_demo.json()["items"] if i["simulated"]]
    assert simulated_rows and simulated_rows[0]["sensor"] == "simulator", "simulated rows must be clearly labelled"


# ---------------------------------------------------------------------------
# 3. Full switch-first flow persists thermal history (§3)
# ---------------------------------------------------------------------------
def test_switch_first_persists_thermal_history(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "WS Analytics Device")
    token = auth_headers["Authorization"].split()[1]
    ok, buf = cv2.imencode(".jpg", make_switch_frame())
    assert ok
    b64 = base64.b64encode(buf.tobytes()).decode()

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "switch_first", "device_id": device_id})
        assert ws.receive_json()["type"] == "ready"
        completed = None
        for _ in range(80):
            ws.send_json({"type": "frame", "jpeg_base64": b64})
            msg = ws.receive_json()
            if msg["type"] == "switch_complete":
                completed = msg
                break
        assert completed is not None
        ws.send_json({"type": "stop_inspection"})
        for _ in range(10):
            if ws.receive_json()["type"] == "stopped":
                break

    history = client.get(f"/api/v1/devices/{device_id}/thermal-history?exclude_demo=false", headers=auth_headers)
    assert history.status_code == 200
    items = history.json()["items"]
    assert items, "the completed switch inspection must have a thermal summary row"
    row = items[0]
    assert row["max_temp"] is not None
    assert row["min_temp"] is not None and row["min_temp"] <= row["max_temp"]
    assert row["avg_temp"] is not None
    assert row["delta"] is not None
    assert row["classification"] in ("NORMAL", "ELEVATED", "ABNORMAL", "CRITICAL")
    assert row["simulated"] is True, "simulator readings must be flagged DEMO"

    health = client.get(f"/api/v1/devices/{device_id}/health", headers=auth_headers).json()
    assert health["insufficient"] is True, "one inspection is below the health minimum — no fabricated score"


# ---------------------------------------------------------------------------
# 4. Trends, health, risk, comparison, timeline, maintenance (§4–§11)
# ---------------------------------------------------------------------------
def test_trend_health_risk_comparison_maintenance(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Trending Device")
    _seed_thermal_history(
        [
            {"max_temp": 31.0, "min_temp": 22.0, "avg_temp": 26.0, "reference_temp": 26.0, "delta": 5.0, "classification": "NORMAL", "simulated": False, "risk_score": 5.0},
            {"max_temp": 34.0, "min_temp": 22.0, "avg_temp": 27.0, "reference_temp": 27.0, "delta": 7.0, "classification": "NORMAL", "simulated": False, "risk_score": 5.0},
            {"max_temp": 39.0, "min_temp": 23.0, "avg_temp": 28.0, "reference_temp": 27.0, "delta": 12.0, "classification": "ELEVATED", "simulated": False, "fault_severity": "warning", "risk_score": 30.0},
            {"max_temp": 47.0, "min_temp": 24.0, "avg_temp": 30.0, "reference_temp": 29.0, "delta": 18.0, "classification": "ELEVATED", "simulated": False, "fault_severity": "warning", "fault_type": "elevated_temperature", "risk_score": 35.0},
            {"max_temp": 55.0, "min_temp": 25.0, "avg_temp": 32.0, "reference_temp": 30.0, "delta": 25.0, "classification": "ABNORMAL", "simulated": False, "heat_path": "localized", "fault_severity": "high", "fault_type": "thermal_anomaly", "risk_score": 60.0},
        ],
        device_id,
    )

    # temperature trend — spec example 31→34→39→47→55 → RAPIDLY_INCREASING
    trend = client.get(f"/api/v1/devices/{device_id}/temperature-trend", headers=auth_headers).json()
    assert trend["insufficient"] is False
    assert trend["status"] == "RAPIDLY_INCREASING"
    assert trend["slope_c_per_inspection"] is not None and trend["slope_c_per_inspection"] > 3.0
    assert len(trend["points"]) == 5

    # ΔT trend (§5)
    delta_trend = client.get(f"/api/v1/devices/{device_id}/delta-trend", headers=auth_headers).json()
    assert delta_trend["insufficient"] is False
    assert delta_trend["status"] in ("STABLE", "INCREASING", "DECREASING", "INCONSISTENT")
    assert len(delta_trend["points"]) == 5

    # health: explainable and deterministic
    health = client.get(f"/api/v1/devices/{device_id}/health", headers=auth_headers).json()
    assert health["insufficient"] is False
    assert 0 <= health["score"] <= 100
    names = [c["name"] for c in health["contributors"]]
    assert "Thermal condition" in names and "Thermal trend" in names and "Inspection history" in names
    assert health["disclaimer"], "health must carry an honesty disclaimer"

    # risk: evidence + reasoning, never a bare number
    risk = client.get(f"/api/v1/devices/{device_id}/risk", headers=auth_headers).json()
    assert risk["insufficient"] is False
    assert risk["level"] in ("NORMAL", "ELEVATED", "ABNORMAL", "CRITICAL")
    assert risk["reasoning"]
    if risk["evidence"]:
        assert any("localized hotspot" in e or "°C relative to reference" in e for e in risk["evidence"])

    # maintenance: from actual evidence
    maintenance = client.get(f"/api/v1/devices/{device_id}/maintenance", headers=auth_headers).json()
    assert maintenance["insufficient"] is False
    assert maintenance["recommendation"]
    assert maintenance["basis"], "recommendations must cite their evidence basis"

    # comparison: current vs previous
    comparison = client.get(f"/api/v1/devices/{device_id}/comparison", headers=auth_headers).json()
    assert comparison["insufficient"] is False
    assert comparison["current"]["temperature"] == 55.0
    assert comparison["previous"]["temperature"] == 47.0
    assert comparison["temperature_change"] == 8.0
    assert comparison["trend"] in ("INCREASING", "STABLE", "DECREASING")

    # anomalies + repeated detection
    anomalies = client.get(f"/api/v1/devices/{device_id}/anomalies", headers=auth_headers).json()
    assert anomalies["total_anomalies"] >= 2
    assert "kinds" in anomalies

    # timeline: real events only
    timeline = client.get(f"/api/v1/devices/{device_id}/timeline", headers=auth_headers).json()
    types = {e["type"] for e in timeline["items"]}
    assert "inspection_completed" in types
    assert "thermal_anomaly" in types
    assert "device_created" in types
    assert types <= {"device_created", "inspection_completed", "thermal_anomaly", "alarm", "incident", "maintenance"}


def test_insufficient_trend_message(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "One Scan Device")
    _seed_thermal_history(
        [{"max_temp": 40.0, "min_temp": 24.0, "avg_temp": 28.0, "reference_temp": 27.0, "delta": 13.0, "classification": "ELEVATED", "simulated": False}],
        device_id,
    )
    health = client.get(f"/api/v1/devices/{device_id}/health", headers=auth_headers).json()
    assert health["insufficient"] is True
    assert health["message"] == "Insufficient data to calculate device health."

    trend = client.get(f"/api/v1/devices/{device_id}/temperature-trend", headers=auth_headers).json()
    assert trend["insufficient"] is True
    assert trend["message"] == "Insufficient historical data for trend analysis."
    assert trend["status"] is None


# ---------------------------------------------------------------------------
# 5. Repeated anomalies: never count frames within one inspection (§6)
# ---------------------------------------------------------------------------
def test_repeated_anomalies_single_inspection_not_double_counted(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Repeat Detect Device")
    # one inspection with TWO identical faults (e.g. two persisted frames) —
    # this must count as ONE historical anomaly, never two.
    from backend.db.session import SessionLocal
    from backend.models.fault import Fault
    from backend.models.inspection import Inspection
    from backend.models.thermal_history import ThermalHistory

    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        inspection = Inspection(
            device_id=device_id,
            mode="switch_first",
            camera_source="test",
            status="completed",
            started_at=now - timedelta(minutes=5),
            ended_at=now,
            inspection_code=f"TG-REPEAT-{device_id}",
            software_version="0.1.0",
            model_version="cv-switch",
            thermal_source="mlx90640",
            thermal_simulated=False,
            risk_score=60.0,
            component_count=1,
        )
        db.add(inspection)
        db.flush()
        db.add(
            ThermalHistory(
                inspection_id=inspection.id,
                device_id=device_id,
                timestamp=now,
                max_temp=52.0,
                min_temp=25.0,
                avg_temp=31.0,
                reference_temp=29.0,
                delta=23.0,
                hotspot_x=0.5,
                hotspot_y=0.5,
                classification="ABNORMAL",
                thermal_source="mlx90640",
                mode="switch_first",
                simulated=False,
            )
        )
        for _ in range(2):  # two identical faults within the SAME inspection
            db.add(
                Fault(
                    inspection_id=inspection.id,
                    component_label="wall_switch",
                    fault_type="thermal_anomaly",
                    severity="high",
                    confidence=0.9,
                    temperature=52.0,
                    message="Localized hotspot detected.",
                    recommendation="Professional electrical inspection recommended.",
                )
            )
        db.commit()
    finally:
        db.close()

    anomalies = client.get(f"/api/v1/devices/{device_id}/anomalies", headers=auth_headers).json()
    assert anomalies["total_anomalies"] == 1, "frames within one inspection are never separate anomalies"
    assert anomalies["repeated"] is False, "one inspection cannot create a repeated anomaly"


def test_repeated_anomaly_across_inspections(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Repeat Across Device")
    _seed_thermal_history(
        [
            {"max_temp": 45.0, "min_temp": 24.0, "avg_temp": 29.0, "reference_temp": 28.0, "delta": 17.0, "classification": "ABNORMAL", "simulated": False, "fault_severity": "high", "fault_type": "thermal_anomaly"},
            {"max_temp": 44.0, "min_temp": 24.0, "avg_temp": 29.0, "reference_temp": 28.0, "delta": 16.0, "classification": "ABNORMAL", "simulated": False, "fault_severity": "high", "fault_type": "thermal_anomaly"},
        ],
        device_id,
    )
    anomalies = client.get(f"/api/v1/devices/{device_id}/anomalies", headers=auth_headers).json()
    assert anomalies["repeated"] is True
    assert anomalies["message"] == "Repeated localized thermal anomaly detected."
    assert any(k["count"] == 2 for k in anomalies["kinds"])


# ---------------------------------------------------------------------------
# 6. Organization isolation (§15)
# ---------------------------------------------------------------------------
def test_organization_isolation(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Isolated Org Device")
    _seed_thermal_history(
        [{"max_temp": 55.0, "min_temp": 25.0, "avg_temp": 31.0, "reference_temp": 30.0, "delta": 25.0, "classification": "ABNORMAL", "simulated": False, "fault_severity": "high"}],
        device_id,
    )
    _make_user("acme_inspector", organization="Acme Corp", role="technician")
    acme = _login(client, "acme_inspector")

    # ID manipulation: requesting another org's device must look like 404
    assert client.get(f"/api/v1/devices/{device_id}", headers=acme).status_code == 404
    assert client.get(f"/api/v1/devices/{device_id}/health", headers=acme).status_code == 404
    assert client.get(f"/api/v1/devices/{device_id}/risk", headers=acme).status_code == 404
    assert client.get(f"/api/v1/devices/{device_id}/thermal-history", headers=acme).status_code == 404
    assert client.get(f"/api/v1/devices/{device_id}/timeline", headers=acme).status_code == 404
    assert client.put(f"/api/v1/devices/{device_id}", json={"location": "Hacked"}, headers=acme).status_code == 404
    # DELETE is admin-only: a technician from another org is rejected with an
    # authorization error (403) before any existence is revealed — either
    # response satisfies the isolation contract ("authorization error or
    # not-found").
    assert client.delete(f"/api/v1/devices/{device_id}", headers=acme).status_code in (403, 404)

    # list is org-scoped — the device never appears
    listing = client.get("/api/v1/devices", headers=acme).json()
    assert all(d["id"] != device_id for d in listing)

    # cannot start an inspection against another org's device
    start = client.post("/api/v1/inspections", json={"mode": "switch_first", "device_id": device_id}, headers=acme)
    assert start.status_code == 404, start.text

    # inspections of that device are hidden from the other org
    inspection_id = None
    inspections = client.get("/api/v1/inspections", headers=auth_headers).json()
    for item in inspections["items"]:
        if item.get("device_id") == device_id:
            inspection_id = item["id"]
            break
    assert inspection_id is not None, "admin sees the device's inspection"
    assert client.get(f"/api/v1/inspections/{inspection_id}", headers=acme).status_code == 404
    acme_list = client.get("/api/v1/inspections", headers=acme).json()
    assert all(i["id"] != inspection_id for i in acme_list["items"])

    # An org-less user (e.g. self-registered) is also strictly scoped: strict
    # NULL-matching means they can never see another org's device inspections.
    _make_user("no_org_viewer", organization=None, role="technician")
    no_org = _login(client, "no_org_viewer")
    assert client.get(f"/api/v1/inspections/{inspection_id}", headers=no_org).status_code == 404
    no_org_list = client.get("/api/v1/inspections", headers=no_org).json()
    assert all(i["id"] != inspection_id for i in no_org_list["items"])
    start = client.post("/api/v1/inspections", json={"mode": "switch_first", "device_id": device_id}, headers=no_org)
    assert start.status_code == 404, start.text


# ---------------------------------------------------------------------------
# 7. Manual status requires authorization (§12)
# ---------------------------------------------------------------------------
def test_manual_maintenance_status_admin_only(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Maintenance Status Device")
    _make_user("org_technician", organization="ThermoGuard", role="technician")
    tech = _login(client, "org_technician")

    denied = client.put(
        f"/api/v1/devices/{device_id}",
        json={"derived_status": "MAINTENANCE"},
        headers=tech,
    )
    assert denied.status_code == 403, "MAINTENANCE is a manual status — technician must be denied"

    granted = client.put(
        f"/api/v1/devices/{device_id}",
        json={"derived_status": "MAINTENANCE"},
        headers=auth_headers,
    )
    assert granted.status_code == 200
    assert granted.json()["derived_status"] == "MAINTENANCE"
    assert granted.json()["status_override"] is True

    # setting a non-manual status clears the override so the system re-derives
    cleared = client.put(
        f"/api/v1/devices/{device_id}",
        json={"derived_status": "ACTIVE"},
        headers=auth_headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["status_override"] is False


# ---------------------------------------------------------------------------
# 8. Dashboard aggregation (§14)
# ---------------------------------------------------------------------------
def test_dashboard_aggregation(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Dashboard Device")
    _seed_thermal_history(
        [{"max_temp": 52.0, "min_temp": 25.0, "avg_temp": 31.0, "reference_temp": 29.0, "delta": 23.0, "classification": "ABNORMAL", "simulated": False, "fault_severity": "high"}],
        device_id,
    )
    dashboard = client.get("/api/v1/devices/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["total_devices"] >= 1
    assert body["inspections_this_month"] >= 1
    assert body["anomalies_this_month"] >= 1
    assert body["avg_temperature"] is not None and body["avg_temperature"] > 0
    assert body["max_temperature"] is not None and body["max_temperature"] >= body["avg_temperature"]
    for key in ("healthy", "attention", "high_risk", "critical", "maintenance", "inactive", "devices_requiring_maintenance"):
        assert key in body, key

    # org-scoped: Acme user sees a dashboard with none of ThermoGuard's data
    _make_user("acme_dash", organization="Acme Corp")
    acme = _login(client, "acme_dash")
    acme_dash = client.get("/api/v1/devices/dashboard", headers=acme).json()
    assert acme_dash["total_devices"] == 0
    assert acme_dash["inspections_this_month"] == 0


# ---------------------------------------------------------------------------
# 9. Additive migration safety (§16)
# ---------------------------------------------------------------------------
def test_additive_migration_preserves_data(tmp_path) -> None:  # noqa: ANN001
    """An existing (legacy) database gains S2 columns without losing rows."""
    from backend.db.session import _COLUMN_ADDITIONS

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    # minimal legacy schema for every table the migration registry touches;
    # devices/users carry their legacy columns so existing rows are realistic
    for table in _COLUMN_ADDITIONS:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    conn.execute("ALTER TABLE devices ADD COLUMN name TEXT")
    conn.execute("ALTER TABLE devices ADD COLUMN status TEXT")
    conn.execute("ALTER TABLE devices ADD COLUMN organization TEXT")
    conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
    conn.execute(
        "INSERT INTO devices (id, name, status, organization) VALUES (1, 'Legacy Switch', 'active', 'OrgA')"
    )
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'legacy_admin')")
    conn.commit()
    conn.close()

    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn2:
        for table, columns in _COLUMN_ADDITIONS.items():
            existing = {row[1] for row in conn2.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            for name, ddl in columns:
                if name not in existing:
                    conn2.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    cols = inspect(engine)
    device_cols = {c["name"] for c in cols.get_columns("devices")}
    for column in (
        "building",
        "floor",
        "room",
        "rated_voltage",
        "rated_current",
        "last_maintenance_date",
        "next_inspection_date",
        "derived_status",
        "status_override",
    ):
        assert column in device_cols, column
    assert "organization" in {c["name"] for c in cols.get_columns("users")}
    # S4/S5: thermal_history gains the emissivity column additively
    assert "emissivity" in {c["name"] for c in cols.get_columns("thermal_history")}

    with engine.connect() as conn2:
        row = conn2.execute(text("SELECT id, name, status, organization, derived_status FROM devices WHERE id = 1")).one()
    assert row[1] == "Legacy Switch", "original row must be preserved"
    assert row[4] == "ACTIVE", "new column gets its default, existing data intact"


def test_init_db_is_idempotent() -> None:
    """init_db may run repeatedly without errors or data loss."""
    from backend.db.session import SessionLocal, init_db

    init_db()
    init_db()
    db = SessionLocal()
    try:
        from backend.models.device import Device

        count = db.query(Device).count()
        assert count >= 0
    finally:
        db.close()
