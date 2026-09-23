"""S1 integration tests — device management, isolation, switch-first WS flow."""
from __future__ import annotations

import base64

import cv2
import numpy as np

from tests.switch_test_utils import make_switch_frame


def _create_device(client, auth_headers, name: str) -> int:  # noqa: ANN001
    resp = client.post(
        "/api/v1/devices",
        json={"name": name, "location": "Lab", "device_type": "wall_switch"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Device CRUD
# ---------------------------------------------------------------------------
def test_devices_crud(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "Living Room Switch")

    listing = client.get("/api/v1/devices", headers=auth_headers)
    assert listing.status_code == 200
    row = next(d for d in listing.json() if d["id"] == device_id)
    assert row["name"] == "Living Room Switch"
    assert row["status"] == "active" and row["risk_level"] == "LOW"
    assert "inspections_count" in row

    detail = client.get(f"/api/v1/devices/{device_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["device_type"] == "wall_switch"
    assert detail.json()["recent_inspections"] == []

    updated = client.put(
        f"/api/v1/devices/{device_id}",
        json={"location": "House A / Living Room", "risk_level": "MEDIUM"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["location"] == "House A / Living Room"
    assert updated.json()["risk_level"] == "MEDIUM"

    deleted = client.delete(f"/api/v1/devices/{device_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/devices/{device_id}", headers=auth_headers).status_code == 404


def test_devices_require_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/devices").status_code == 401
    assert client.post("/api/v1/devices", json={"name": "X"}).status_code == 401


# ---------------------------------------------------------------------------
# Device isolation — inspections must never mix between devices
# ---------------------------------------------------------------------------
def test_inspection_device_isolation(client, auth_headers) -> None:  # noqa: ANN001
    dev_a = _create_device(client, auth_headers, "Device A")
    dev_b = _create_device(client, auth_headers, "Device B")

    for dev in (dev_a, dev_b):
        start = client.post(
            "/api/v1/inspections",
            json={"mode": "switch_first", "device_id": dev, "camera_source": "test"},
            headers=auth_headers,
        )
        assert start.status_code == 201, start.text
        assert start.json()["device_id"] == dev
        client.post(f"/api/v1/inspections/{start.json()['id']}/stop", json={}, headers=auth_headers)

    only_a = client.get(f"/api/v1/inspections?device_id={dev_a}", headers=auth_headers).json()
    only_b = client.get(f"/api/v1/inspections?device_id={dev_b}", headers=auth_headers).json()
    assert only_a["items"], "device A should have inspections"
    assert all(i["device_id"] == dev_a for i in only_a["items"]), "device A shows only its own data"
    assert all(i["device_id"] == dev_b for i in only_b["items"]), "device B shows only its own data"

    detail = client.get(f"/api/v1/inspections/{only_a['items'][0]['id']}", headers=auth_headers).json()
    assert detail["device_name"] == "Device A"


# ---------------------------------------------------------------------------
# Full switch-first WebSocket flow
# ---------------------------------------------------------------------------
def test_switch_first_ws_flow(client, auth_headers) -> None:  # noqa: ANN001
    device_id = _create_device(client, auth_headers, "WS Switch")
    token = auth_headers["Authorization"].split()[1]

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
            assert "data" in msg and "state" in msg["data"]
        assert completed is not None, "switch_complete must arrive"
        assert completed["data"]["thermal_available"]
        assert completed["data"]["classification"] != "UNAVAILABLE"
        assert completed["data"]["evidence"], "findings must carry evidence"
        assert completed["device_id"] == device_id

        ws.send_json({"type": "stop_inspection"})
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "stopped":
                break

    # the record is persisted, device-linked, and complete
    detail = client.get(f"/api/v1/inspections/{inspection_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["mode"] == "switch_first"
    assert body["device_id"] == device_id
    assert body["device_name"] == "WS Switch"
    assert body["status"] == "completed"
    assert any(d["label"] == "wall_switch" for d in body["detections"]), "confirmed switch must be recorded"
    assert body["temperature_series"], "thermal time-series must be persisted"
    assert body["risk_score"] > 0
    assert body["thermal_simulated"] is True, "simulated readings must be labeled on the record"


def test_ws_switch_first_requires_auth(client) -> None:  # noqa: ANN001
    """A WebSocket without a valid token is closed before accepting."""
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws/inspect") as ws:
            ws.receive_json()


# ---------------------------------------------------------------------------
# Service-level finalize (unit-style, uses the test DB)
# ---------------------------------------------------------------------------
def test_switch_service_finalize(test_db) -> None:  # noqa: ANN001
    from ai.switch.thermal import ThermalScanResult
    from backend.models.alarm import Alarm
    from backend.models.detection import Detection
    from backend.models.device import Device
    from backend.models.fault import Fault
    from backend.models.temperature_history import TemperatureReading
    from backend.services.inspection_service import InspectionService
    from backend.services.switch_service import SwitchInspectionService

    device = Device(name="Test Switch", location="Lab", device_type="wall_switch")
    test_db.add(device)
    test_db.flush()

    inspection = InspectionService(test_db).start_inspection(
        user_id=None, mode="switch_first", device_id=device.id, camera_source="test"
    )
    assert inspection.device_id == device.id

    result = ThermalScanResult(
        thermal_available=True,
        switch_temp=52.0,
        wall_temp=29.0,
        ambient=26.0,
        delta_vs_wall=23.0,
        max_temp=55.0,
        hotspot=(0.5, 0.5),
        heat_path="extended",
        trend_c_per_min=1.2,
        trend_delta_c=3.0,
        rapid_increase=False,
        stability_c=0.4,
        classification="ABNORMAL",
        risk_score=60.0,
        evidence=["Switch at 52.0 °C vs nearby wall 29.0 °C (Δ +23.0 °C)"],
        series=[{"t": 1.0, "switch": 50.0, "wall": 28.0}, {"t": 2.0, "switch": 52.0, "wall": 29.0}],
        message="Abnormal thermal pattern detected near the electrical switch.",
    )
    status = {
        "switch_bbox": [260, 190, 380, 310],
        "switch_confidence": 0.9,
        "thermal_source": "test-sim",
        "thermal_simulated": True,
        "complete": result.to_dict(),
    }
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    SwitchInspectionService(test_db).finalize(inspection, status, frame, frame.copy())

    test_db.refresh(inspection)
    assert inspection.device_id == device.id
    assert inspection.component_count == 1
    assert inspection.risk_score == 60.0
    assert inspection.thermal_simulated is True

    det = test_db.query(Detection).filter(Detection.inspection_id == inspection.id).all()
    assert len(det) == 1 and det[0].label == "wall_switch"
    assert det[0].health == "high"

    faults = test_db.query(Fault).filter(Fault.inspection_id == inspection.id).all()
    assert len(faults) == 1
    assert faults[0].fault_type == "thermal_anomaly"
    assert faults[0].severity == "high"

    readings = test_db.query(TemperatureReading).filter(TemperatureReading.inspection_id == inspection.id).all()
    assert len(readings) == 2, "the time-series must be persisted"

    alarms = test_db.query(Alarm).filter(Alarm.inspection_id == inspection.id).all()
    assert len(alarms) == 1 and alarms[0].severity == "high"
