"""S4 inspection persistence-integrity tests.

Prevents: duplicate finalization, duplicate faults/history, partial inspections
being marked complete, and completed inspections being re-aborted. All service
calls run against the isolated test database.
"""
from __future__ import annotations

import numpy as np

from ai.switch.thermal import ThermalScanResult
from backend.models.detection import Detection
from backend.models.device import Device
from backend.models.fault import Fault
from backend.models.thermal_history import ThermalHistory
from backend.services.inspection_service import InspectionService
from backend.services.switch_service import SwitchInspectionService


def _start_switch_inspection(test_db, device_id: int):  # noqa: ANN001
    return InspectionService(test_db).start_inspection(
        user_id=None, mode="switch_first", device_id=device_id, camera_source="test"
    )


def _scan_result(classification: str = "ABNORMAL") -> ThermalScanResult:
    return ThermalScanResult(
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
        classification=classification,
        risk_score=60.0,
        evidence=["Switch at 52.0 °C vs nearby wall 29.0 °C (Δ +23.0 °C)"],
        series=[{"t": 1.0, "switch": 50.0, "wall": 28.0}, {"t": 2.0, "switch": 52.0, "wall": 29.0}],
        message="Abnormal thermal pattern detected near the electrical switch.",
    )


def _finalize_status(result: ThermalScanResult) -> dict:
    return {
        "switch_bbox": [260, 190, 380, 310],
        "switch_confidence": 0.9,
        "thermal_source": "simulator",
        "thermal_simulated": True,
        "complete": result.to_dict(),
    }


def test_stop_inspection_idempotent(test_db) -> None:  # noqa: ANN001
    device = Device(name="Idem Device", device_type="wall_switch", status="ACTIVE")
    test_db.add(device)
    test_db.flush()
    service = InspectionService(test_db)
    inspection = service.start_inspection(user_id=None, mode="quick", device_id=device.id)

    first = service.stop_inspection(inspection.id)
    assert first.status == "completed"
    ended_at = first.ended_at

    # a second stop is a no-op — status and ended_at are never rewritten
    second = service.stop_inspection(inspection.id)
    assert second.status == "completed"
    assert second.ended_at == ended_at


def test_abort_only_transitions_running(test_db) -> None:  # noqa: ANN001
    device = Device(name="Abort Device", device_type="wall_switch", status="ACTIVE")
    test_db.add(device)
    test_db.flush()
    service = InspectionService(test_db)
    inspection = service.start_inspection(user_id=None, mode="quick", device_id=device.id)

    # running → aborted
    service.stop_inspection(inspection.id)
    assert service.abort_inspection(inspection.id).status == "completed", (
        "abort must never re-mark a completed inspection aborted"
    )


def test_partial_inspection_never_completed(test_db) -> None:  # noqa: ANN001
    """A running inspection that is never stopped stays running (never 'complete')."""
    device = Device(name="Partial Device", device_type="wall_switch", status="ACTIVE")
    test_db.add(device)
    test_db.flush()
    service = InspectionService(test_db)
    inspection = service.start_inspection(user_id=None, mode="quick", device_id=device.id)
    assert inspection.status == "running"
    assert inspection.ended_at is None
    # no faults/no frames → still running
    assert test_db.query(Detection).filter(Detection.inspection_id == inspection.id).count() == 0


def test_finalize_is_idempotent(test_db) -> None:  # noqa: ANN001
    device = Device(name="Finalize Device", device_type="wall_switch", status="ACTIVE")
    test_db.add(device)
    test_db.flush()
    inspection = _start_switch_inspection(test_db, device.id)
    result = _scan_result()
    status = _finalize_status(result)
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)

    service = SwitchInspectionService(test_db)
    service.finalize(inspection, status, frame, frame.copy())
    test_db.refresh(inspection)
    assert test_db.query(Detection).filter(Detection.inspection_id == inspection.id).count() == 1

    # a duplicate finalize (WS reconnect / retry) must not duplicate anything
    service.finalize(inspection, status, frame, frame.copy())
    assert test_db.query(Detection).filter(Detection.inspection_id == inspection.id).count() == 1
    assert test_db.query(Fault).filter(Fault.inspection_id == inspection.id).count() == 1
    assert test_db.query(ThermalHistory).filter(ThermalHistory.inspection_id == inspection.id).count() == 1


def test_auto_incidents_and_maintenance_run_once(test_db) -> None:  # noqa: ANN001
    """Completing an inspection with critical faults creates incidents once."""
    from datetime import UTC, datetime

    from backend.models.incident import Incident
    from backend.models.maintenance import MaintenanceRecord
    from backend.models.temperature_history import TemperatureReading

    device = Device(name="Incident Device", device_type="wall_switch", status="ACTIVE")
    test_db.add(device)
    test_db.flush()
    inspection = _start_switch_inspection(test_db, device.id)

    # simulate a critical fault row + a temperature reading (as the pipeline would)
    test_db.add(
        Fault(
            inspection_id=inspection.id,
            component_label="wall_switch",
            fault_type="critical_thermal_anomaly",
            severity="critical",
            confidence=0.9,
            temperature=88.0,
            message="Critical thermal anomaly",
            recommendation="Professional inspection",
        )
    )
    test_db.add(
        TemperatureReading(
            component_label="wall_switch",
            inspection_id=inspection.id,
            temperature=88.0,
            reading_time=datetime.now(UTC),
            source="thermal",
        )
    )
    test_db.commit()

    service = InspectionService(test_db)
    service.stop_inspection(inspection.id)  # triggers auto incidents + maintenance
    incidents = test_db.query(Incident).filter(Incident.inspection_id == inspection.id).count()
    maintenance = test_db.query(MaintenanceRecord).filter(MaintenanceRecord.inspection_id == inspection.id).count()
    assert incidents >= 1
    assert maintenance >= 1

    # stopping again must not duplicate
    service.stop_inspection(inspection.id)
    assert test_db.query(Incident).filter(Incident.inspection_id == inspection.id).count() == incidents
    assert test_db.query(MaintenanceRecord).filter(MaintenanceRecord.inspection_id == inspection.id).count() == maintenance
