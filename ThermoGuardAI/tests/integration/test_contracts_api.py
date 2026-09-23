"""Integration tests for data contracts, API schemas, and persistence migrations.

Verifies:
1. Backward compatibility of DetectionOut, FaultOut, and IncidentOut schemas.
2. Persistence of enriched incidents and simulated flags in database.
3. Idempotency of additive schema migrations (_ensure_columns).
4. Full API endpoint serialization of enriched inspection details.
"""
from datetime import UTC, datetime

from sqlalchemy import select

from backend.db.session import _ensure_columns
from backend.models.component import Component
from backend.models.device import Device
from backend.models.incident import Incident
from backend.models.inspection import Inspection
from backend.models.panel import Panel
from backend.models.temperature_history import TemperatureReading
from backend.schemas.contracts import IncidentStatus, SeverityLevel
from backend.schemas.incident import IncidentCreate, IncidentDetail, IncidentResolution
from backend.schemas.inspection import DetectionOut, FaultOut, IncidentOut


class TestSchemaBackwardCompatibility:
    """Ensure existing consumers are not broken by additive contract fields."""

    def test_detection_out_legacy_payload(self):
        legacy = {
            "id": 1,
            "label": "circuit_breaker",
            "confidence": 0.95,
            "bbox": "[10, 20, 100, 200]",
            "temperature": 45.2,
            "health": "healthy",
            "frame_index": 3,
        }
        item = DetectionOut.model_validate(legacy)
        assert item.id == 1
        assert item.label == "circuit_breaker"
        # Additive fields default to None
        assert item.component_id is None
        assert item.session_track_id is None
        assert item.provenance is None
        assert item.thermal_quality is None
        assert item.coverage is None

    def test_fault_out_legacy_payload(self):
        legacy = {
            "id": 1,
            "fault_type": "overheating",
            "severity": "critical",
            "confidence": 0.88,
            "temperature": 75.5,
            "message": "Thermal rise detected",
            "recommendation": "Check load",
        }
        item = FaultOut.model_validate(legacy)
        assert item.fault_type == "overheating"
        assert item.component_id is None
        assert item.delta_t is None
        assert item.hypotheses == []

    def test_incident_out_legacy_payload(self):
        legacy = {
            "id": 1,
            "code": "INC-20260923-01",
            "fault_type": "loose_connection",
            "severity": "high",
            "occurred_at": "2026-09-23T10:00:00Z",
        }
        item = IncidentOut.model_validate(legacy)
        assert item.code == "INC-20260923-01"
        assert item.status == "OPEN"
        assert item.device_id is None
        assert item.panel_id is None
        assert item.component_id is None
        assert item.delta_t is None
        assert item.evidence_thermal_path is None
        assert item.resolved_at is None

    def test_incident_out_enriched_payload(self):
        enriched = {
            "id": 2,
            "code": "INC-20260923-02",
            "status": "INVESTIGATING",
            "fault_type": "overloaded_circuit",
            "severity": "critical",
            "device_id": 5,
            "panel_id": 3,
            "component_id": 42,
            "component_code": "B1",
            "delta_t": 35.4,
            "peer_comparison": "32.1C above peer breaker B2",
            "evidence_thermal_path": "/media/evidence/thermal_inc_02.png",
            "occurred_at": "2026-09-23T10:15:00Z",
        }
        item = IncidentOut.model_validate(enriched)
        assert item.status == "INVESTIGATING"
        assert item.device_id == 5
        assert item.component_code == "B1"
        assert item.delta_t == 35.4
        assert item.evidence_thermal_path == "/media/evidence/thermal_inc_02.png"

    def test_incident_schemas_lifecycle(self):
        create_payload = IncidentCreate(
            fault_type="loose_connection",
            severity="CRITICAL",
            temperature=85.0,
            delta_t=40.0,
        )
        assert create_payload.fault_type == "loose_connection"

        resolution_payload = IncidentResolution(
            status="RESOLVED",
            resolution_notes="Torqued connection to specification.",
            resolved_by="lead_inspector",
        )
        assert resolution_payload.status == "RESOLVED"
        assert resolution_payload.resolved_by == "lead_inspector"

        detail_payload = IncidentDetail(
            id=1,
            code="INC-1",
            fault_type="loose_connection",
            severity="CRITICAL",
            occurred_at=datetime.now(UTC),
            status="RESOLVED",
            resolution_notes="Torqued connection to specification.",
            resolved_by="lead_inspector",
        )
        assert detail_payload.code == "INC-1"
        assert detail_payload.visible_evidence == []
        assert detail_payload.fault_hypotheses == []



class TestDatabaseContractsAndPersistence:
    """Verify database schema additions, column migrations, and ORM operations."""

    def test_ensure_columns_idempotent(self, test_db):
        """_ensure_columns must succeed without error on existing schema."""
        _ensure_columns()
        # Run second time to guarantee idempotency
        _ensure_columns()

    def test_incident_persistence_and_lifecycle(self, test_db):
        """Verify Incident ORM model with new foreign keys and lifecycle audit fields."""
        device = Device(name="Test Switchboard", location="Basement", device_type="switchboard")
        test_db.add(device)
        test_db.flush()

        panel = Panel(name="Main Distribution Panel", code="PNL-TEST-01")
        test_db.add(panel)
        test_db.flush()

        comp = Component(panel_id=panel.id, label="Main Feeder Breaker", component_type="circuit_breaker", code="B1")
        test_db.add(comp)
        test_db.flush()

        now = datetime.now(UTC)
        incident = Incident(
            code="INC-TEST-001",
            device_id=device.id,
            panel_id=panel.id,
            component_id=comp.id,
            status=IncidentStatus.OPEN.value,
            fault_type="overheating",
            severity=SeverityLevel.CRITICAL.value,
            component_label="Main Feeder Breaker",
            temperature=82.5,
            delta_t=45.2,
            peer_comparison="40.0C hotter than parallel feeder",
            occurred_at=now,
            evidence_thermal_path="/media/evidence/test_thermal.png",
            suggested_action="De-energize and inspect contacts",
        )
        test_db.add(incident)
        test_db.commit()

        # Query back and verify relations and attributes
        saved = test_db.execute(select(Incident).where(Incident.code == "INC-TEST-001")).scalar_one()
        assert saved.device_id == device.id
        assert saved.panel_id == panel.id
        assert saved.component_id == comp.id
        assert saved.status == "OPEN"
        assert saved.delta_t == 45.2
        assert saved.device.name == "Test Switchboard"
        assert saved.panel.code == "PNL-TEST-01"
        assert saved.component.code == "B1"

        # Validate through IncidentOut schema
        out = IncidentOut.model_validate(saved)
        assert out.status == "OPEN"
        assert out.device_id == device.id
        assert out.delta_t == 45.2

        # Update lifecycle to RESOLVED
        saved.status = IncidentStatus.RESOLVED.value
        saved.resolved_at = datetime.now(UTC)
        saved.resolved_by = "lead_electrician"
        saved.resolution_notes = "Torqued loose lug to 4 N·m, thermal scan verified nominal."
        test_db.commit()

        updated = test_db.execute(select(Incident).where(Incident.code == "INC-TEST-001")).scalar_one()
        assert updated.status == "RESOLVED"
        assert updated.resolved_by == "lead_electrician"
        assert "Torqued loose lug" in updated.resolution_notes

    def test_temperature_history_simulated_separation(self, test_db):
        """Verify simulated column prevents synthetic readings from polluting real measurements."""
        now = datetime.now(UTC)
        real_reading = TemperatureReading(
            component_label="Breaker B1",
            temperature=42.0,
            reading_time=now,
            source="flir_one",
            simulated=False,
        )
        sim_reading = TemperatureReading(
            component_label="Breaker B1",
            temperature=99.0,
            reading_time=now,
            source="simulator",
            simulated=True,
        )
        test_db.add_all([real_reading, sim_reading])
        test_db.commit()

        real_count = test_db.scalar(
            select(TemperatureReading).where(TemperatureReading.simulated.is_(False)).with_only_columns(TemperatureReading.id)
        )
        assert real_count is not None

        # Verify real-only query filters out the 99°C simulated spike
        max_real_temp = test_db.scalar(
            select(TemperatureReading.temperature)
            .where(TemperatureReading.component_label == "Breaker B1", TemperatureReading.simulated.is_(False))
            .order_by(TemperatureReading.temperature.desc())
            .limit(1)
        )
        assert max_real_temp == 42.0


class TestAPIEndpointSerialization:
    """Verify that existing FastAPI endpoints return enriched models cleanly."""

    def test_get_inspection_detail_serialization(self, client, auth_headers, test_db):
        # Create an inspection with an incident
        panel = Panel(name="API Test Panel", code="PNL-API-01")
        test_db.add(panel)
        test_db.flush()

        comp = Component(panel_id=panel.id, label="B1 Breaker", component_type="circuit_breaker", code="B1")
        test_db.add(comp)
        test_db.flush()

        now = datetime.now(UTC)
        insp = Inspection(
            status="completed",
            mode="manual",
            camera_source="test",
            started_at=now,
            ended_at=now,
            panel_id=panel.id,
            frames_processed=10,
            avg_fps=15.0,
            risk_score=75.0,
            component_count=1,
        )
        test_db.add(insp)
        test_db.flush()

        inc = Incident(
            code=f"INC-{insp.id}-01",
            inspection_id=insp.id,
            panel_id=panel.id,
            component_id=comp.id,
            status="OPEN",
            fault_type="overloaded_circuit",
            severity="critical",
            component_label="B1 Breaker",
            temperature=78.5,
            delta_t=52.0,
            occurred_at=now,
        )
        test_db.add(inc)
        test_db.commit()

        # Call endpoint
        response = client.get(f"/api/v1/inspections/{insp.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert "incidents" in data
        assert len(data["incidents"]) >= 1
        incident_data = data["incidents"][0]
        assert incident_data["code"] == f"INC-{insp.id}-01"
        assert incident_data["status"] == "OPEN"
        assert incident_data["delta_t"] == 52.0
        assert incident_data["panel_id"] == panel.id
        assert incident_data["component_id"] == comp.id
