"""S4 end-to-end flow (single chained integration test).

Stage coverage — this test exercises the FULL production workflow in one flow:

    USER LOGIN
    → SELECT/CREATE DEVICE
    → START INSPECTION (WebSocket)
    → BROWSER CAMERA (simulated frame transport)
    → SWITCH DETECTED → SWITCH STABLE → ROI LOCKED
    → THERMAL INITIALIZATION → THERMAL BASELINE → THERMAL SCANNING
    → HOTSPOT/TREND/ANOMALY ANALYSIS → RISK → RECOMMENDATION
    → INSPECTION COMPLETE → PERSIST
    → DEVICE HISTORY + THERMAL HISTORY
    → THERMAL STATUS (authoritative backend source)
    → ANALYTICS → REPORT GENERATION → AUTHORIZED REPORT DOWNLOAD

Runs in DEMO / SIMULATED THERMAL mode (no physical hardware) and verifies
every stage honestly. Complementary coverage lives in the coordinated
integration suite: test_switch_first (WS switch flow), test_device_analytics
(device history/analytics), test_report_generation (PDF download).
"""
from __future__ import annotations

import base64

import cv2

from backend.core.config import get_settings
from tests.switch_test_utils import make_switch_frame


def test_s4_full_e2e_flow(client) -> None:  # noqa: ANN001
    settings = get_settings()
    # 1) USER LOGIN
    login = client.post(
        "/api/v1/auth/login",
        json={"username": settings.seed_admin_username, "password": settings.seed_admin_password},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2) SELECT/CREATE DEVICE
    device = client.post(
        "/api/v1/devices",
        json={"name": "S4 E2E Switch", "location": "E2E Lab", "device_type": "wall_switch"},
        headers=headers,
    )
    assert device.status_code == 201, device.text
    device_id = device.json()["id"]

    # 3) START INSPECTION (WebSocket, browser-camera workflow simulated)
    ok, buf = cv2.imencode(".jpg", make_switch_frame())
    assert ok
    b64 = base64.b64encode(buf.tobytes()).decode()

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "switch_first", "device_id": device_id})
        msg = ws.receive_json()
        assert msg["type"] == "ready", msg
        inspection_id = msg["inspection_id"]

        completed = None
        for _ in range(80):
            ws.send_json({"type": "frame", "jpeg_base64": b64})
            msg = ws.receive_json()
            if msg["type"] == "switch_complete":
                completed = msg
                break
            assert msg["type"] == "switch_status", msg
            data = msg["data"]
            # once thermal analysis is active, DEMO mode is always explicit
            if data.get("thermal_available"):
                assert data["thermal_simulated"] is True, "DEMO mode must be labelled"
        assert completed is not None, "inspection must reach completion"
        assert completed["data"]["thermal_available"]
        assert completed["data"]["classification"] in ("NORMAL", "ELEVATED", "ABNORMAL", "CRITICAL")
        assert completed["data"]["data_quality"] in ("VALID", "LOW_CONFIDENCE")
        assert completed["data"]["emissivity"] is None, "simulator must not invent emissivity"
        ws.send_json({"type": "stop_inspection"})
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "stopped":
                break

    # 4) PERSIST — inspection completed and device-linked
    detail = client.get(f"/api/v1/inspections/{inspection_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "completed"
    assert body["device_id"] == device_id
    assert body["thermal_simulated"] is True
    assert any(d["label"] == "wall_switch" for d in body["detections"])

    # 5) DEVICE HISTORY + THERMAL HISTORY (exclude_demo hides simulated rows)
    history = client.get(f"/api/v1/devices/{device_id}/history", headers=headers)
    assert history.status_code == 200
    thermal_history = client.get(f"/api/v1/devices/{device_id}/thermal-history", headers=headers).json()
    # DEMO rows are excluded from real analytics by default — never mixed in.
    assert thermal_history["items"] == [], "simulated rows must be excluded from device analytics"
    assert thermal_history["simulated_excluded"] == 1, "excluded DEMO row must be disclosed"
    assert body["temperature_series"], "thermal time-series persisted for the record"

    # 6) THERMAL STATUS — authoritative backend decision
    status = client.get("/api/v1/thermal/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["hardware"] == "DEMO / SIMULATED THERMAL"

    # 7) ANALYTICS + REPORT + AUTHORIZED DOWNLOAD
    analytics = client.get("/api/v1/analytics/summary", headers=headers)
    assert analytics.status_code == 200
    report = client.post(f"/api/v1/reports/inspections/{inspection_id}", json={}, headers=headers)
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]
    assert "file_path" not in report.json(), "report responses must not leak filesystem paths"
    assert "file_available" in report.json(), "report responses expose boolean availability"
    assert report.json()["file_available"] is True
    download = client.get(f"/api/v1/reports/{report_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.content[:4] == b"%PDF"
    # unauthorized download fails safely
    assert client.get(f"/api/v1/reports/{report_id}/download").status_code == 401

    # 8) ORGANIZATION ISOLATION — a foreign-org user must fail closed on the
    # device, the inspection, the thermal history and the report download
    # (404, never 403 — existence must not leak).
    from backend.core.security import hash_password
    from backend.db.session import SessionLocal
    from backend.models.user import User

    db = SessionLocal()
    try:
        db.add(
            User(
                username="s5_foreign_org",
                email="s5_foreign@example.test",
                full_name="S5 Foreign Org",
                hashed_password=hash_password("Password123!"),
                role="technician",
                organization="Foreign Corp",
            )
        )
        db.commit()
    finally:
        db.close()
    foreign_login = client.post(
        "/api/v1/auth/login",
        json={"username": "s5_foreign_org", "password": "Password123!"},
    )
    assert foreign_login.status_code == 200, foreign_login.text
    foreign_headers = {"Authorization": f"Bearer {foreign_login.json()['access_token']}"}

    assert client.get(f"/api/v1/devices/{device_id}", headers=foreign_headers).status_code == 404
    assert client.get(f"/api/v1/inspections/{inspection_id}", headers=foreign_headers).status_code == 404
    assert (
        client.get(f"/api/v1/devices/{device_id}/thermal-history", headers=foreign_headers).status_code == 404
    ), "foreign org must never see the device's thermal history"
    assert client.get(f"/api/v1/reports/{report_id}/download", headers=foreign_headers).status_code == 404
    assert client.get(f"/api/v1/reports/{report_id}", headers=foreign_headers).status_code == 404
    # no device list leakage
    foreign_devices = client.get("/api/v1/devices", headers=foreign_headers).json()
    assert all(d["id"] != device_id for d in foreign_devices)

    # 9) THERMAL STATUS is authoritative + authenticated
    assert client.get("/api/v1/thermal/status").status_code == 401, "thermal status requires auth"
    foreign_status = client.get("/api/v1/thermal/status", headers=foreign_headers).json()
    assert foreign_status["measurement"] in ("MEASURED", "SIMULATED", "UNAVAILABLE", "INVALID")
    assert foreign_status["hardware"] in ("REAL SENSOR", "DEMO / SIMULATED THERMAL")
