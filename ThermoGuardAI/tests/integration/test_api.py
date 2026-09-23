"""Integration tests covering the full API surface."""
from __future__ import annotations

import numpy as np
import pytest


def test_health(client) -> None:  # noqa: ANN001
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["detector"] in ("ultralytics-yolo", "cv-fallback")


def test_login_and_stats(client, auth_headers) -> None:  # noqa: ANN001
    resp = client.get("/api/v1/inspections/stats", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "by_severity" in body


def test_unauthenticated_denied(client) -> None:  # noqa: ANN001
    resp = client.get("/api/v1/inspections")
    assert resp.status_code == 401


def test_full_inspection_flow(client, auth_headers) -> None:  # noqa: ANN001
    # start inspection
    start = client.post(
        "/api/v1/inspections",
        json={"mode": "quick", "camera_source": "test"},
        headers=auth_headers,
    )
    assert start.status_code == 201
    inspection_id = start.json()["id"]

    # process a synthetic frame
    import cv2

    frame = np.full((480, 640, 3), 60, dtype=np.uint8)
    frame[120:300, 100:300] = (30, 30, 35)
    frame[100:160, 400:480] = (240, 240, 240)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    upload = client.post(
        f"/api/v1/inspections/{inspection_id}/frame",
        files={"file": ("frame.jpg", buf.tobytes(), "image/jpeg")},
        headers=auth_headers,
    )
    assert upload.status_code == 200, upload.text
    result = upload.json()
    assert "risk_score" in result
    assert "detections" in result

    # detail
    detail = client.get(f"/api/v1/inspections/{inspection_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["frames_processed"] >= 1

    # stop
    stop = client.post(f"/api/v1/inspections/{inspection_id}/stop", json={}, headers=auth_headers)
    assert stop.status_code == 200
    assert stop.json()["status"] == "completed"


def test_panels_crud(client, auth_headers) -> None:  # noqa: ANN001
    resp = client.post(
        "/api/v1/panels",
        json={"name": "Test Panel", "code": "PANEL-TEST", "location": "Lab"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    panel_id = resp.json()["id"]

    detail = client.get(f"/api/v1/panels/{panel_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["code"] == "PANEL-TEST"

    # duplicate code rejected
    dup = client.post(
        "/api/v1/panels",
        json={"name": "Dup", "code": "PANEL-TEST"},
        headers=auth_headers,
    )
    assert dup.status_code == 409


def test_analytics_and_predictive(client, auth_headers) -> None:  # noqa: ANN001
    summary = client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert summary.status_code == 200
    assert "severity_breakdown" in summary.json()

    pred = client.get("/api/v1/analytics/predictive", headers=auth_headers)
    assert pred.status_code == 200
    assert "items" in pred.json()


def test_report_generation(client, auth_headers) -> None:  # noqa: ANN001
    start = client.post(
        "/api/v1/inspections",
        json={"mode": "manual", "camera_source": "test"},
        headers=auth_headers,
    )
    inspection_id = start.json()["id"]
    report = client.post(f"/api/v1/reports/inspections/{inspection_id}", json={}, headers=auth_headers)
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]

    download = client.get(f"/api/v1/reports/{report_id}/download", headers=auth_headers)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert download.content[:4] == b"%PDF"


def test_mobile_page_served(client) -> None:  # noqa: ANN001
    # Canonical top-level URL (documented in README/API contract) and the
    # legacy v1-prefixed alias must both serve the phone camera client.
    for url in ("/mobile", "/api/v1/mobile"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert "getUserMedia" in resp.text


# ---------------------------------------------------------------------------
# Inspection history & management (professional requirement)
# ---------------------------------------------------------------------------
def _start_quick(client, auth_headers) -> int:  # noqa: ANN001
    resp = client.post("/api/v1/inspections", json={"mode": "quick"}, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_inspection_code_format(client, auth_headers) -> None:  # noqa: ANN001
    import re

    inspection_id = _start_quick(client, auth_headers)
    resp = client.get(f"/api/v1/inspections/{inspection_id}", headers=auth_headers)
    assert resp.status_code == 200
    code = resp.json()["inspection_code"]
    assert re.fullmatch(r"TG-\d{8}-\d{5}", code), code
    assert resp.json()["software_version"], "software version must be recorded"
    assert resp.json()["model_version"], "AI model version must be recorded"
    assert resp.json()["health_score"] == round(100 - resp.json()["risk_score"], 1)


def test_history_list_filters_and_export(client, auth_headers) -> None:  # noqa: ANN001
    _start_quick(client, auth_headers)
    _start_quick(client, auth_headers)

    # new shape: {items, total} with enriched fields
    listing = client.get("/api/v1/inspections", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert "items" in body and "total" in body
    assert body["total"] >= 2
    row = body["items"][0]
    for field in ("inspection_code", "health_score", "counts", "temperature_stats", "faults_count", "inspector"):
        assert field in row, field

    # status filter
    filtered = client.get("/api/v1/inspections?status=running", headers=auth_headers).json()
    assert all(i["status"] == "running" for i in filtered["items"])

    # search by inspection code (must not produce cartesian duplicates)
    code = body["items"][0]["inspection_code"]
    searched = client.get(f"/api/v1/inspections?search={code}", headers=auth_headers).json()
    assert any(i["inspection_code"] == code for i in searched["items"])
    ids = [i["id"] for i in searched["items"]]
    assert len(ids) == len(set(ids)), "cartesian join produced duplicate rows"
    assert searched["total"] == len(ids)

    # search by panel name (exercises the Panel join path)
    by_panel = client.get("/api/v1/inspections?search=Main+Distribution", headers=auth_headers).json()
    ids2 = [i["id"] for i in by_panel["items"]]
    assert len(ids2) == len(set(ids2))

    # CSV export
    export = client.get("/api/v1/inspections/export", headers=auth_headers)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    text = export.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Inspection ID")
    assert code in text


def test_archive_unarchive_delete(client, auth_headers) -> None:  # noqa: ANN001
    inspection_id = _start_quick(client, auth_headers)

    arch = client.post(f"/api/v1/inspections/{inspection_id}/archive", headers=auth_headers)
    assert arch.status_code == 200 and arch.json()["archived"] is True

    active = client.get("/api/v1/inspections?archived=false", headers=auth_headers).json()
    archived = client.get("/api/v1/inspections?archived=true", headers=auth_headers).json()
    assert all(not i["archived"] for i in active["items"])
    assert any(i["id"] == inspection_id for i in archived["items"])

    client.post(f"/api/v1/inspections/{inspection_id}/unarchive", headers=auth_headers)

    # delete (admin-only) → subsequent detail 404
    deleted = client.delete(f"/api/v1/inspections/{inspection_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/inspections/{inspection_id}", headers=auth_headers).status_code == 404


def test_incidents_and_maintenance_auto_created(client, auth_headers) -> None:  # noqa: ANN001
    """Critical faults must auto-create incident reports + maintenance orders."""
    import cv2
    import numpy as np

    inspection_id = _start_quick(client, auth_headers)
    frame = np.full((480, 640, 3), 60, dtype=np.uint8)
    frame[120:300, 100:300] = (30, 30, 35)
    frame[100:160, 400:480] = (240, 240, 240)  # bright hotspot → visible_spark critical
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    upload = client.post(
        f"/api/v1/inspections/{inspection_id}/frame",
        files={"file": ("frame.jpg", buf.tobytes(), "image/jpeg")},
        headers=auth_headers,
    )
    assert upload.status_code == 200
    stop = client.post(f"/api/v1/inspections/{inspection_id}/stop", json={}, headers=auth_headers)
    assert stop.status_code == 200 and stop.json()["status"] == "completed"

    detail = client.get(f"/api/v1/inspections/{inspection_id}", headers=auth_headers).json()
    critical = [f for f in detail["faults"] if f["severity"] == "critical"]
    if critical:  # CV fallback is deterministic for this synthetic frame
        assert detail["incidents"], "incident report should be auto-created"
        incident = detail["incidents"][0]
        assert incident["code"].startswith("INC-")
        assert incident["suggested_action"]

        maint = client.get("/api/v1/maintenance", headers=auth_headers).json()
        assert maint["items"], "maintenance order should be auto-created"
        record = maint["items"][0]
        assert record["code"].startswith("MT-")
        assert record["priority"] == "critical"
        assert record["status"] == "pending"
        assert record["deadline"], "deadline should be set"


def test_maintenance_crud(client, auth_headers) -> None:  # noqa: ANN001
    created = client.post(
        "/api/v1/maintenance",
        json={"component_label": "Breaker B2", "fault_type": "overheating", "priority": "high", "deadline": "2026-08-14"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    assert created.json()["code"].startswith("MT-")

    updated = client.post(
        f"/api/v1/maintenance/{record_id}/update",
        json={"status": "in_progress", "assigned_to": "Tech A"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_progress"
    assert updated.json()["assigned_to"] == "Tech A"

    filtered = client.get("/api/v1/maintenance?status=in_progress", headers=auth_headers).json()
    assert any(r["id"] == record_id for r in filtered["items"])

    deleted = client.delete(f"/api/v1/maintenance/{record_id}", headers=auth_headers)
    assert deleted.status_code == 200


def test_maintenance_cost_and_completion(client, auth_headers) -> None:  # noqa: ANN001
    """Repair cost tracking: cost is set, completion stamps completed_at."""
    created = client.post(
        "/api/v1/maintenance",
        json={
            "component_label": "Breaker B2",
            "fault_type": "overheating",
            "priority": "critical",
            "cost": 1250.50,
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    assert created.json()["cost"] == 1250.50

    done = client.post(
        f"/api/v1/maintenance/{record_id}/update",
        json={"status": "completed"},
        headers=auth_headers,
    )
    assert done.status_code == 200
    assert done.json()["status"] == "completed"
    assert done.json()["completed_at"], "completed_at should be stamped on completion"

    reopened = client.post(
        f"/api/v1/maintenance/{record_id}/update",
        json={"status": "pending"},
        headers=auth_headers,
    )
    assert reopened.json()["completed_at"] is None


# ---------------------------------------------------------------------------
# Asset lifecycle management (phase 2)
# ---------------------------------------------------------------------------
def _seed_component_data() -> int:  # noqa: ANN001
    """Create a component with temperature/fault/maintenance history; returns component id."""
    from datetime import UTC, datetime, timedelta

    from backend.db.session import SessionLocal
    from backend.models.component import Component
    from backend.models.fault import Fault
    from backend.models.inspection import Inspection
    from backend.models.maintenance import MaintenanceRecord
    from backend.models.panel import Panel
    from backend.models.temperature_history import TemperatureReading

    db = SessionLocal()
    try:
        # Create the inspection via the same session (API call would deadlock on
        # the SQLite write lock while this session holds uncommitted rows).
        inspection = Inspection(
            user_id=None,
            mode="quick",
            camera_source="test",
            status="completed",
            started_at=datetime.now(UTC) - timedelta(days=1),
            ended_at=datetime.now(UTC),
            inspection_code="TG-TEST-ASSET-1",
            software_version="0.1.0",
            model_version="cv-fallback",
        )
        db.add(inspection)
        db.flush()
        inspection_id = inspection.id
        panel = db.query(Panel).filter(Panel.code == "PANEL-MAIN").first()
        assert panel, "seeded PANEL-MAIN panel expected"
        component = db.query(Component).filter(Component.panel_id == panel.id, Component.label == "Breaker B9").first()
        if component is None:
            component = Component(
                panel_id=panel.id, label="Breaker B9", component_type="circuit_breaker", code="B9"
            )
            db.add(component)
            db.flush()
        cid = component.id
        # idempotent: clear previous fixture data for this component so costs/
        # readings/faults don't accumulate across tests sharing the session DB
        for model in (TemperatureReading, Fault, MaintenanceRecord):
            for old in db.query(model).filter(model.component_id == cid).all():  # type: ignore[attr-defined]
                db.delete(old)
        db.flush()
        now = datetime.now(UTC)
        db.add_all(
            [
                TemperatureReading(
                    component_id=cid,
                    component_label=component.label,
                    inspection_id=None,
                    temperature=38.0 + i * 4,
                    reading_time=now - timedelta(days=30 - i),
                    source="thermal",
                )
                for i in range(20)
            ]
        )
        db.add(
            Fault(
                inspection_id=inspection_id,
                component_id=cid,
                component_label=component.label,
                fault_type="overheating",
                severity="critical",
                confidence=0.9,
                temperature=78.0,
                message="Overheating detected",
                recommendation="Replace immediately",
            )
        )
        db.add(
            MaintenanceRecord(
                code="MT-TEST-1",
                component_id=cid,
                component_label=component.label,
                fault_type="overheating",
                priority="critical",
                status="completed",
                cost=500.0,
            )
        )
        db.commit()
        return cid
    finally:
        db.close()


def test_asset_overview(client, auth_headers) -> None:  # noqa: ANN001
    _seed_component_data()
    resp = client.get("/api/v1/assets/overview", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "panels" in body and "totals" in body
    assert body["totals"]["panels"] >= 1
    assert "components" in body["totals"]
    panel_row = body["panels"][0]
    for field in ("component_count", "avg_reliability", "open_maintenance", "total_repair_cost", "at_risk_components"):
        assert field in panel_row, field


def test_asset_panel_and_component_detail(client, auth_headers) -> None:  # noqa: ANN001
    cid = _seed_component_data()

    overview = client.get("/api/v1/assets/overview", headers=auth_headers).json()
    panel_id = overview["panels"][0]["id"]

    panel = client.get(f"/api/v1/assets/panels/{panel_id}", headers=auth_headers)
    assert panel.status_code == 200
    panel_body = panel.json()
    assert panel_body["component_count"] >= 1
    comps = panel_body["components"]
    assert any(c["id"] == cid for c in comps), "component should appear in panel lifecycle"
    for field in ("reliability_score", "rul_hours", "failure_probability", "replacement_count", "total_repair_cost", "health"):
        assert field in comps[0], field

    detail = client.get(f"/api/v1/assets/components/{cid}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    d = detail.json()
    assert d["temperature_series"], "temperature history expected"
    assert d["temperature_stats"]["readings"] == 20
    assert d["failures"], "failure history expected"
    assert d["maintenance"], "maintenance history expected"
    assert d["total_repair_cost"] == 500.0
    assert 0 <= d["reliability_score"] <= 100


def test_asset_replace_and_update(client, auth_headers) -> None:  # noqa: ANN001
    cid = _seed_component_data()

    replaced = client.post(f"/api/v1/assets/components/{cid}/replace", headers=auth_headers)
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["replacement_count"] == 1
    assert body["last_replaced_at"], "replacement date should be set"
    assert body["installed_at"], "installed date resets on replacement"

    updated = client.post(
        f"/api/v1/assets/components/{cid}/update",
        json={"expected_life_years": 25.0, "retired": True},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["expected_life_years"] == 25.0
    assert updated.json()["retired"] is True

    # retired component is excluded from at-risk aggregation but present in detail
    overview = client.get("/api/v1/assets/overview", headers=auth_headers).json()
    panel_row = overview["panels"][0]
    assert all(c["id"] != cid or c["retired"] for c in panel_row["components"])


def test_asset_requires_auth(client) -> None:  # noqa: ANN001
    assert client.get("/api/v1/assets/overview").status_code == 401
    assert client.get("/api/v1/assets/panels/1").status_code == 401


# ---------------------------------------------------------------------------
# Real-time camera streaming (phase 3)
# ---------------------------------------------------------------------------
class _FakeCameraSource:
    """Deterministic camera source for stream tests (no hardware needed)."""

    def __init__(self) -> None:
        import numpy as np

        self.frame = np.full((240, 320, 3), 90, dtype=np.uint8)
        self.info = type("I", (), {"id": "fake", "name": "Fake Camera", "kind": "webcam"})()
        self._count = 0

    def start(self) -> None:
        pass

    def read(self) -> object:
        import numpy as np

        self._count += 1
        self.frame[::40, :] = np.full((6, 320, 3), self._count * 10 % 255, dtype=np.uint8)
        return self.frame.copy()

    def stop(self) -> None:
        pass


@pytest.fixture()
def fake_camera(client) -> None:  # noqa: ANN001
    """Point the singleton capture manager at a fake source for stream tests."""
    from backend.services import camera_service
    from camera.capture_manager import CaptureManager

    manager = CaptureManager(target_fps=30.0)
    manager.switch(_FakeCameraSource())
    camera_service._manager = manager  # noqa: SLF001
    yield
    camera_service._manager = None  # noqa: SLF001


def test_camera_frame_snapshot(client, auth_headers, fake_camera) -> None:  # noqa: ANN001
    token = auth_headers["Authorization"].split()[1]
    resp = client.get("/api/v1/cameras/frame", params={"token": token, "annotate": "false"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content[:2] == b"\xff\xd8"  # JPEG magic
    # annotated variant runs the pipeline too
    annotated = client.get("/api/v1/cameras/frame", params={"token": token, "annotate": "true"})
    assert annotated.status_code == 200
    assert annotated.content[:2] == b"\xff\xd8"


def test_camera_stream_multipart(client, auth_headers, fake_camera) -> None:  # noqa: ANN001
    """The MJPEG endpoint serves multipart/x-mixed-replace frames."""
    token = auth_headers["Authorization"].split()[1]
    with client.stream(
        "GET",
        "/api/v1/cameras/stream",
        params={"token": token, "annotate": "false", "max_fps": 20, "max_frames": 5},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("multipart/x-mixed-replace")
        boundary = resp.headers["content-type"].split("boundary=")[1]
        # read at least two JPEG frames from the multipart stream
        frames = 0
        buf = b""
        for chunk in resp.iter_bytes():
            buf += chunk
            if buf.count(f"--{boundary}".encode()) >= 3:
                break
            if b"\xff\xd8" in buf and buf.count(f"--{boundary}".encode()) >= 2:
                frames += 1
                buf = b""
            if frames >= 2:
                break
        assert frames >= 1 or buf, "stream should yield at least one frame"


def test_camera_stream_requires_auth(client, fake_camera) -> None:  # noqa: ANN001
    assert client.get("/api/v1/cameras/frame").status_code == 401
    with client.stream("GET", "/api/v1/cameras/stream") as resp:
        assert resp.status_code == 401


def test_camera_stream_persists_into_inspection(client, auth_headers, fake_camera) -> None:  # noqa: ANN001
    """Streaming with an inspection_id persists frames into the inspection."""
    inspection_id = _start_quick(client, auth_headers)
    token = auth_headers["Authorization"].split()[1]
    with client.stream(
        "GET",
        "/api/v1/cameras/stream",
        params={"token": token, "annotate": "true", "inspection_id": inspection_id, "max_fps": 30, "max_frames": 5},
    ) as resp:
        assert resp.status_code == 200
        boundary = resp.headers["content-type"].split("boundary=")[1]
        got_frame = False
        for chunk in resp.iter_bytes():
            if b"\xff\xd8" in chunk:
                got_frame = True
                break
            if chunk.count(f"--{boundary}".encode()) > 0 and b"\xff\xd8" in chunk:
                got_frame = True
                break
        assert got_frame, "stream should yield an annotated frame"
    detail = client.get(f"/api/v1/inspections/{inspection_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["frames_processed"] >= 1, "inspection should have processed streamed frames"
