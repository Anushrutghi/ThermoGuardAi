"""Tests for ReportLab reporting workflow with immutable inspection snapshots.

Verifies:
1. Reports are generated strictly from immutable snapshots with canonical SHA-256 verification.
2. Observed physical indicators are strictly separated from inferred diagnostic hypotheses.
3. Source provenance, capture/analysis timing, and radiometric calibration quality are stamped.
4. Real versus simulated status is explicitly stamped with warning banners.
5. Missing operating context (ambient, load) and unresolved limitations are disclosed.
6. Automatic safety certification is explicitly disclaimed.
7. Signature placeholder is explicitly described as a manual physical sign-off (not cryptographic).
8. Cross-organization access to reports and downloads is blocked (404).
9. QR verification endpoint adheres to public traceability access policy.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.db.session import SessionLocal, init_db
from backend.main import app
from backend.models.device import Device
from backend.models.inspection import Inspection
from backend.models.panel import Panel
from backend.models.report import Report
from backend.models.user import User
from backend.schemas.snapshot import (
    InspectionSnapshot,
    SnapshotComponent,
    SnapshotIncident,
    SnapshotInferredCause,
    SnapshotObservedIndicator,
    SnapshotOperatingContext,
    SnapshotPanel,
    SnapshotProvenance,
    SnapshotScope,
)
from backend.services.report_service import ReportService
from reports.pdf_generator import ReportData, build_report_story, extract_story_text, generate_pdf


# ============================================================================
# 1. Snapshot Integrity & Canonical Hash Tests
# ============================================================================

def test_immutable_snapshot_creation_and_hash_integrity() -> None:
    """Verifies that an immutable snapshot generates a deterministic SHA-256 and detects tampering."""
    snapshot = InspectionSnapshot(
        report_id="TG-TEST-001",
        inspection_id=42,
        inspection_code="INSP-000042",
        inspector="engineer_alice",
        risk_score=45.0,
        scope=SnapshotScope(mode="switch_first", duration_seconds=120.0, frames_processed=1800),
        panel=SnapshotPanel(code="SWBD-01", name="Main Switchboard Alpha", location="Basement Electrical Room"),
        provenance=SnapshotProvenance(
            camera_name="Sony Optical Sensor",
            thermal_source="FLIR Lepton 3.5",
            thermal_simulated=False,
            hardware_type="REAL SENSOR",
            calibration_quality="CALIBRATED_RADIOMETRIC",
            emissivity=0.95,
            ambient_temp_c=23.5,
        ),
        observed_indicators=[
            SnapshotObservedIndicator(component_label="Breaker B1", max_temp_c=58.4, delta_t_c=34.9),
        ],
        inferred_causes=[
            SnapshotInferredCause(fault_type="loose_terminal", severity="warning", confidence=0.88),
        ],
    )
    c_hash = snapshot.finalize_checksum()
    assert len(c_hash) == 64
    assert snapshot.verify_integrity() is True

    # Mutating any field after finalization must invalidate integrity
    snapshot.risk_score = 99.0
    assert snapshot.verify_integrity() is False


# ============================================================================
# 2. PDF Generation & Content Separation Tests
# ============================================================================

def test_report_generation_from_snapshot(tmp_path: Path) -> None:
    """Verifies PDF generation directly from an immutable snapshot."""
    snapshot = InspectionSnapshot(
        report_id="TG-TEST-002",
        inspection_id=43,
        inspection_code="INSP-000043",
        inspector="tech_bob",
        risk_score=72.0,
        panel=SnapshotPanel(code="DP-02", name="Distribution Panel Beta", location="Floor 2 Utility Closet"),
        provenance=SnapshotProvenance(
            thermal_source="Seek Thermal CompactPRO",
            thermal_simulated=False,
            hardware_type="REAL SENSOR",
            calibration_quality="CALIBRATED_RADIOMETRIC",
            emissivity=0.92,
            ambient_temp_c=21.0,
        ),
        observed_indicators=[
            SnapshotObservedIndicator(component_label="Contactor K1", max_temp_c=68.2, avg_temp_c=64.1, delta_t_c=47.2),
            SnapshotObservedIndicator(component_label="Terminal T1", max_temp_c=74.5, avg_temp_c=71.0, delta_t_c=53.5),
        ],
        inferred_causes=[
            SnapshotInferredCause(
                fault_type="thermal_overload",
                severity="critical",
                confidence=0.94,
                message="Thermal rise exceeds IEEE C37 limit by 24.5 °C.",
                recommendation="De-energize circuit and inspect contactor contacts.",
            )
        ],
        incidents=[
            SnapshotIncident(
                code="INC-2026-0043",
                fault_type="thermal_overload",
                severity="critical",
                stage="ESCALATED",
                occurred_at=datetime.now().isoformat(),
                acknowledged_by="tech_bob",
                override_reason="Elevated to critical after verification under 85% load.",
            )
        ],
        unresolved_limitations=[
            "Thermal sensor resolution cannot resolve sub-terminal wire contact interfaces.",
        ],
        recommended_followup=[
            "Licensed electrician to perform de-energized torque audit using calibrated wrench.",
        ],
    )
    snapshot.finalize_checksum()

    data = ReportData(snapshot=snapshot)
    out_pdf = tmp_path / "test_report.pdf"
    generate_pdf(data, out_pdf)

    assert out_pdf.exists()
    assert out_pdf.stat().st_size > 1000
    assert out_pdf.read_bytes().startswith(b"%PDF-")

    # Read PDF text to verify content
    story = build_report_story(data)
    full_text = extract_story_text(story)

    # 1. Scope and panel identification
    assert "Distribution Panel Beta" in full_text
    assert "DP-02" in full_text
    assert "TG-TEST-002" in full_text

    # 2. Separation: Observed Indicators vs Inferred Hypotheses
    assert "1. Observed Physical Indicators" in full_text
    assert "Contactor K1" in full_text
    assert "68.2 °C" in full_text

    assert "2. Inferred Diagnostic Hypotheses" in full_text
    assert "Thermal Overload" in full_text
    assert "94.0%" in full_text

    # 3. Incident Lifecycle & Reviewer Actions
    assert "3. Incident Lifecycle & Reviewer Actions" in full_text
    assert "INC-2026-0043" in full_text
    assert "ESCALATED" in full_text
    assert "tech_bob" in full_text

    # 4. Limitations and Follow-up
    assert "4. Unresolved Observational Limitations" in full_text
    assert "cannot resolve sub-terminal wire contact interfaces" in full_text
    assert "5. Recommended Qualified Follow-up" in full_text

    # 5. Non-certification and manual signature disclaimers
    assert "NON-CERTIFICATION NOTICE" in full_text
    assert "does NOT certify electrical equipment safety" in full_text
    assert "does NOT constitute a cryptographic digital signature" in full_text


def test_simulated_and_missing_context_disclosures(tmp_path: Path) -> None:
    """Verifies that simulated data and missing operating context produce prominent warnings."""
    snapshot = InspectionSnapshot(
        report_id="TG-TEST-SIM",
        inspection_id=44,
        inspection_code="INSP-000044",
        inspector="demo_user",
        risk_score=35.0,
        panel=SnapshotPanel(code="PANEL-SIM", name="Simulated Switchboard"),
        provenance=SnapshotProvenance(
            thermal_source="thermal-simulator",
            thermal_simulated=True,
            hardware_type="DEMO / SIMULATED",
            calibration_quality="SIMULATED",
        ),
        operating_context=SnapshotOperatingContext(
            operating_state="UNKNOWN",
            missing_context_flags=["MISSING_AMBIENT_TEMPERATURE", "MISSING_ELECTRICAL_LOAD"],
        ),
    )
    snapshot.finalize_checksum()

    data = ReportData(snapshot=snapshot)
    out_pdf = tmp_path / "simulated_report.pdf"
    generate_pdf(data, out_pdf)

    assert out_pdf.exists()
    assert out_pdf.read_bytes().startswith(b"%PDF-")

    story = build_report_story(data)
    full_text = extract_story_text(story)

    # Must contain prominent simulated warning
    assert "SIMULATED THERMAL DATA NOTICE" in full_text
    assert "No radiometric physical thermal sensor was connected" in full_text

    # Must contain missing context disclosure
    assert "MISSING CONTEXT DISCLOSURE" in full_text
    assert "Missing Ambient Temperature" in full_text
    assert "Missing Electrical Load" in full_text


# ============================================================================
# 3. Multi-Tenant Authorization & Download Security Tests
# ============================================================================

def test_cross_organization_report_download_blocked() -> None:
    """Verifies that a user from Organization B cannot download or access a report belonging to Organization A."""
    init_db()
    client = TestClient(app)
    db = SessionLocal()
    try:
        # Create Org A User & Device & Inspection
        user_a = User(
            username=f"org_a_user_{datetime.now().timestamp()}",
            email=f"user_a_{datetime.now().timestamp()}@test.com",
            hashed_password=hash_password("secret123"),
            role="admin",
            organization="OrgAlpha",
            is_active=True,
        )
        # Create Org B User
        user_b = User(
            username=f"org_b_user_{datetime.now().timestamp()}",
            email=f"user_b_{datetime.now().timestamp()}@test.com",
            hashed_password=hash_password("secret123"),
            role="admin",
            organization="OrgBeta",
            is_active=True,
        )
        db.add_all([user_a, user_b])
        db.commit()

        panel_a = Panel(
            name="Alpha Incomer",
            code=f"PANEL-A-{datetime.now().timestamp()}",
            organization="OrgAlpha",
        )
        device_a = Device(
            name="Alpha Feeder",
            device_type="switchboard",
            organization="OrgAlpha",
            status="ACTIVE",
        )
        db.add_all([panel_a, device_a])
        db.commit()

        insp_a = Inspection(
            device_id=device_a.id,
            panel_id=panel_a.id,
            user_id=user_a.id,
            mode="continuous",
            status="completed",
            risk_score=20.0,
            started_at=datetime.now(),
        )
        db.add(insp_a)
        db.commit()

        # Generate report for Org A
        service = ReportService(db)
        report_a = service.generate_for_inspection(insp_a.id, title="Alpha Inspection Report")

        token_a = create_access_token(user_a.id)
        token_b = create_access_token(user_b.id)

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Org A user can access their report
        resp_a = client.get(f"/api/v1/reports/{report_a.id}", headers=headers_a)
        assert resp_a.status_code == 200, resp_a.text
        assert resp_a.json()["snapshot_sha256"] == report_a.snapshot_sha256

        # Org B user accessing Org A report receives 404 (not 403, to prevent existence leaks)
        resp_b = client.get(f"/api/v1/reports/{report_a.id}", headers=headers_b)
        assert resp_b.status_code == 404

        # Org B download attempt is blocked (404)
        down_b = client.get(f"/api/v1/reports/{report_a.id}/download", headers=headers_b)
        assert down_b.status_code == 404

        # Org A snapshot endpoint works
        snap_a = client.get(f"/api/v1/reports/{report_a.id}/snapshot", headers=headers_a)
        assert snap_a.status_code == 200
        assert snap_a.json()["report_id"] == report_a.snapshot_sha256 or snap_a.json()["inspection_id"] == insp_a.id

        # Public QR verification endpoint works safely without leaking files
        verify_resp = client.get(f"/api/v1/reports/{report_a.id}/verify?hash={report_a.snapshot_sha256[:16]}")
        assert verify_resp.status_code == 200
        data = verify_resp.json()
        assert data["verified"] is True
        assert data["snapshot_sha256"] == report_a.snapshot_sha256
        assert "certify" in data["disclaimer"]

    finally:
        db.close()
