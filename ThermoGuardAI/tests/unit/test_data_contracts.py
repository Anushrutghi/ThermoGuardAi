"""Unit tests for canonical data contracts.

Verifies:
1. Strict non-zero missing temperature enforcement (physical 0.0 °C is accepted,
   missing temperature MUST be None with MISSING_* status).
2. Separation of measured, simulated, and missing temperatures.
3. Distinction between visible optical evidence and inferred fault hypotheses.
4. Latency and UTC timestamp semantics.
5. Serialization and deserialization integrity across all contracts.
"""
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.schemas.contracts import (
    ComponentConditionState,
    ComponentStateContract,
    CoverageStatus,
    EvidenceBackedIncidentContract,
    FaultHypothesis,
    IncidentStatus,
    MeasurementContract,
    ObservationContract,
    ObservationProvenance,
    SeverityLevel,
    TemperatureReadingContract,
    TemperatureStatus,
    ThermalDataQuality,
    VisibleEvidenceItem,
)


class TestTemperatureReadingContract:
    """Test suite verifying temperature contract semantics and zero-temperature rules."""

    def test_valid_measured_temperature(self):
        reading = TemperatureReadingContract(
            status=TemperatureStatus.MEASURED,
            temperature_c=54.2,
            ambient_c=22.0,
            delta_t_c=32.2,
            emissivity=0.95,
            provenance=ObservationProvenance.MEASURED,
            sensor_id="flir-lepton-35",
            measurement_confidence=0.98,
        )
        assert reading.status == TemperatureStatus.MEASURED
        assert reading.temperature_c == 54.2
        assert reading.delta_t_c == 32.2
        assert reading.provenance == ObservationProvenance.MEASURED

    def test_physical_zero_celsius_is_permitted(self):
        """Physical 0.0 °C is a valid temperature reading in cold environments."""
        reading = TemperatureReadingContract(
            status=TemperatureStatus.MEASURED,
            temperature_c=0.0,
            ambient_c=-5.0,
            delta_t_c=5.0,
            provenance=ObservationProvenance.MEASURED,
        )
        assert reading.temperature_c == 0.0
        assert reading.status == TemperatureStatus.MEASURED

    def test_subzero_temperature_permitted(self):
        """Sub-zero temperatures (e.g. walk-in freezer switchgear) are valid."""
        reading = TemperatureReadingContract(
            status=TemperatureStatus.MEASURED,
            temperature_c=-12.5,
            ambient_c=-15.0,
            delta_t_c=2.5,
            provenance=ObservationProvenance.MEASURED,
        )
        assert reading.temperature_c == -12.5

    def test_valid_missing_temperature(self):
        """Missing temperature MUST have temperature_c=None with a MISSING_* status."""
        for missing_status in (
            TemperatureStatus.MISSING_NO_SENSOR,
            TemperatureStatus.MISSING_INVALID_FRAME,
            TemperatureStatus.MISSING_OCCLUDED,
            TemperatureStatus.MISSING_OUT_OF_RANGE,
        ):
            reading = TemperatureReadingContract(
                status=missing_status,
                temperature_c=None,
            )
            assert reading.status == missing_status
            assert reading.temperature_c is None

    def test_reject_zero_as_missing_temperature(self):
        """Missing temperature encoded as 0.0 °C MUST be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TemperatureReadingContract(
                status=TemperatureStatus.MISSING_NO_SENSOR,
                temperature_c=0.0,
            )
        assert "indicates missing data; temperature_c must be None" in str(exc_info.value)

    def test_reject_numeric_value_with_missing_status(self):
        """Supplying any numeric temperature with a MISSING_* status must fail."""
        with pytest.raises(ValidationError) as exc_info:
            TemperatureReadingContract(
                status=TemperatureStatus.MISSING_OCCLUDED,
                temperature_c=45.0,
            )
        assert "indicates missing data; temperature_c must be None" in str(exc_info.value)

    def test_reject_none_temperature_with_measured_status(self):
        """MEASURED status requires a numeric temperature reading."""
        with pytest.raises(ValidationError) as exc_info:
            TemperatureReadingContract(
                status=TemperatureStatus.MEASURED,
                temperature_c=None,
            )
        assert "requires a numeric temperature_c, but got None" in str(exc_info.value)

    def test_reject_none_temperature_with_simulated_status(self):
        """SIMULATED status requires a numeric temperature reading."""
        with pytest.raises(ValidationError) as exc_info:
            TemperatureReadingContract(
                status=TemperatureStatus.SIMULATED,
                temperature_c=None,
            )
        assert "requires a numeric temperature_c, but got None" in str(exc_info.value)

    def test_simulated_provenance_separation(self):
        """Synthetic provenance cannot masquerade as MEASURED status."""
        with pytest.raises(ValidationError) as exc_info:
            TemperatureReadingContract(
                status=TemperatureStatus.MEASURED,
                temperature_c=65.0,
                provenance=ObservationProvenance.SYNTHETIC,
            )
        assert "Synthetic provenance cannot have status MEASURED" in str(exc_info.value)

    def test_simulated_status_sets_synthetic_provenance(self):
        """SIMULATED status automatically normalizes provenance to SYNTHETIC."""
        reading = TemperatureReadingContract(
            status=TemperatureStatus.SIMULATED,
            temperature_c=72.0,
        )
        assert reading.provenance == ObservationProvenance.SYNTHETIC

    def test_emissivity_bounds(self):
        with pytest.raises(ValidationError):
            TemperatureReadingContract(
                status=TemperatureStatus.MEASURED,
                temperature_c=30.0,
                emissivity=0.005,  # below 0.01
            )
        with pytest.raises(ValidationError):
            TemperatureReadingContract(
                status=TemperatureStatus.MEASURED,
                temperature_c=30.0,
                emissivity=1.05,  # above 1.00
            )


class TestVisibleEvidenceItem:
    """Test suite for verifiable optical evidence."""

    def test_valid_evidence_item(self):
        item = VisibleEvidenceItem(
            anomaly_type="burn_mark",
            confidence=0.94,
            bbox=[0.12, 0.34, 0.45, 0.67],
            description="Localized carbonization around terminal screw",
            evidence_image_path="/media/evidence/crop_terminal_01.jpg",
        )
        assert item.anomaly_type == "burn_mark"
        assert item.confidence == 0.94
        assert len(item.bbox) == 4

    def test_confidence_validation(self):
        with pytest.raises(ValidationError):
            VisibleEvidenceItem(anomaly_type="corrosion", confidence=1.5)


class TestFaultHypothesis:
    """Test suite verifying separation of diagnostic hypotheses from raw data."""

    def test_valid_fault_hypothesis(self):
        hypo = FaultHypothesis(
            fault_type="loose_connection",
            confidence=0.88,
            severity=SeverityLevel.WARNING,
            inferred_from=["delta_t_c", "visible_burn_mark", "peer_comparison"],
            evidence_summary="Point hotspot at screw terminal with 35°C delta over adjacent phase breaker.",
            recommended_action="De-energize panel and torque screw terminal to 2.5 N·m manufacturer spec.",
        )
        assert hypo.fault_type == "loose_connection"
        assert hypo.severity == SeverityLevel.WARNING
        assert "delta_t_c" in hypo.inferred_from
        assert hypo.recommended_action is not None


class TestObservationContract:
    """Test suite for atomic frame observations."""

    def test_valid_observation_roundtrip(self):
        capture_time = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
        analysis_time = datetime(2026, 9, 23, 10, 0, 0, 85000, tzinfo=UTC)  # +85 ms

        obs = ObservationContract(
            panel_id=1,
            panel_code="PNL-MAIN-01",
            component_id=42,
            component_code="B1",
            component_label="circuit_breaker",
            session_track_id=107,
            frame_index=15,
            frame_id="sha256-a1b2c3d4",
            capture_timestamp=capture_time,
            analysis_timestamp=analysis_time,
            analysis_age_ms=85.0,
            sensor_id="mlx90640-0",
            model_version="yolo11n-tg-v2.1",
            provenance=ObservationProvenance.MEASURED,
            bbox=[0.1, 0.2, 0.4, 0.5],
            detection_confidence=0.96,
            measurement=MeasurementContract(
                temperature=TemperatureReadingContract(
                    status=TemperatureStatus.MEASURED,
                    temperature_c=68.4,
                    ambient_c=24.1,
                    delta_t_c=44.3,
                    emissivity=0.95,
                    provenance=ObservationProvenance.MEASURED,
                ),
                thermal_quality=ThermalDataQuality.VALID,
                coverage=CoverageStatus.FULL,
                distance_m=0.8,
            ),
            visible_evidence=[
                VisibleEvidenceItem(
                    anomaly_type="discoloration",
                    confidence=0.85,
                    description="Yellowish discoloration on breaker plastic casing",
                )
            ],
            fault_hypotheses=[
                FaultHypothesis(
                    fault_type="overloaded_circuit",
                    confidence=0.91,
                    severity=SeverityLevel.CRITICAL,
                    inferred_from=["delta_t_c", "body_heat_spread"],
                    evidence_summary="Whole-body heating exceeding 65°C threshold",
                    recommended_action="Measure load current with clamp meter",
                )
            ],
            coverage_status=CoverageStatus.FULL,
            insufficient_evidence=False,
        )

        assert obs.panel_code == "PNL-MAIN-01"
        assert obs.component_code == "B1"
        assert obs.session_track_id == 107
        assert obs.measurement.temperature.temperature_c == 68.4
        assert len(obs.visible_evidence) == 1
        assert len(obs.fault_hypotheses) == 1

        # Test JSON serialization roundtrip
        json_data = obs.model_dump_json()
        restored = ObservationContract.model_validate_json(json_data)
        assert restored.component_code == "B1"
        assert restored.measurement.temperature.temperature_c == 68.4

    def test_utc_normalization(self):
        """Naive datetimes must be coerced to UTC."""
        naive_capture = datetime(2026, 9, 23, 10, 0, 0)
        naive_analysis = datetime(2026, 9, 23, 10, 0, 0, 50000)

        obs = ObservationContract(
            component_label="terminal_block",
            capture_timestamp=naive_capture,
            analysis_timestamp=naive_analysis,
            analysis_age_ms=50.0,
            bbox=[0.0, 0.0, 0.5, 0.5],
            measurement=MeasurementContract(
                temperature=TemperatureReadingContract(
                    status=TemperatureStatus.MISSING_NO_SENSOR,
                    temperature_c=None,
                )
            ),
        )
        assert obs.capture_timestamp.tzinfo == UTC
        assert obs.analysis_timestamp.tzinfo == UTC

    def test_reject_analysis_before_capture(self):
        """Analysis timestamp cannot occur before capture timestamp."""
        capture_time = datetime(2026, 9, 23, 10, 0, 5, tzinfo=UTC)
        analysis_time = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)

        with pytest.raises(ValidationError) as exc_info:
            ObservationContract(
                component_label="fuse",
                capture_timestamp=capture_time,
                analysis_timestamp=analysis_time,
                analysis_age_ms=0.0,
                bbox=[0.1, 0.1, 0.2, 0.2],
                measurement=MeasurementContract(
                    temperature=TemperatureReadingContract(
                        status=TemperatureStatus.MISSING_NO_SENSOR,
                        temperature_c=None,
                    )
                ),
            )
        assert "cannot precede capture_timestamp" in str(exc_info.value)


class TestComponentStateContract:
    """Test suite for persistent component condition contracts."""

    def test_component_state_tracking(self):
        state = ComponentStateContract(
            component_id=10,
            component_code="B3",
            panel_id=2,
            label="Inverter Feeder Breaker",
            component_type="circuit_breaker",
            condition_state=ComponentConditionState.SUSPECT,
            last_observation_time=datetime(2026, 9, 23, 10, 30, tzinfo=UTC),
            current_temperature_c=58.3,
            historical_max_temp_c=69.1,
            baseline_temp_c=35.0,
            open_incident_count=1,
            installed_at="2024-03-15",
            expected_life_years=15.0,
            replacement_count=0,
        )
        assert state.component_code == "B3"
        assert state.condition_state == ComponentConditionState.SUSPECT
        assert state.open_incident_count == 1


class TestEvidenceBackedIncidentContract:
    """Test suite for verifiable evidence-backed incidents."""

    def test_incident_lifecycle_and_evidence(self):
        now = datetime.now(UTC)
        incident = EvidenceBackedIncidentContract(
            incident_code="INC-20260923-1-01",
            status=IncidentStatus.OPEN,
            inspection_id=5,
            device_id=2,
            panel_id=1,
            component_id=12,
            component_label="Main Contactor",
            component_code="K1",
            fault_type="overheating_contacts",
            severity=SeverityLevel.CRITICAL,
            temperature=TemperatureReadingContract(
                status=TemperatureStatus.MEASURED,
                temperature_c=89.5,
                ambient_c=25.0,
                delta_t_c=64.5,
            ),
            peer_delta_c=42.0,
            visible_evidence=[
                VisibleEvidenceItem(
                    anomaly_type="thermal_discoloration",
                    confidence=0.88,
                    description="Severe discoloration around contact points",
                )
            ],
            fault_hypotheses=[
                FaultHypothesis(
                    fault_type="contact_pitting_and_wear",
                    confidence=0.92,
                    severity=SeverityLevel.CRITICAL,
                    inferred_from=["temperature_c", "peer_delta_c", "visible_evidence"],
                    evidence_summary="Contactor pole 2 is 42°C hotter than poles 1 and 3.",
                    recommended_action="Replace contact kit or complete contactor assembly.",
                )
            ],
            occurred_at=now,
            screenshot_path="/media/evidence/rgb_inc_01.jpg",
            annotated_path="/media/evidence/annotated_inc_01.jpg",
            evidence_thermal_path="/media/evidence/thermal_inc_01.png",
            inspector="lead_technician",
            suggested_action="Isolate supply and service contactor",
        )

        assert incident.status == IncidentStatus.OPEN
        assert incident.temperature.temperature_c == 89.5
        assert incident.peer_delta_c == 42.0
        assert incident.component_code == "K1"
        assert len(incident.fault_hypotheses) == 1

        # Resolve incident
        resolved_time = now + timedelta(hours=2)
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = resolved_time
        incident.resolved_by = "certified_engineer"
        incident.resolution_notes = "Replaced pitted contact tips, torqued terminals to 3 N·m, post-test normal at 31°C."

        assert incident.status == IncidentStatus.RESOLVED
        assert incident.resolved_by == "certified_engineer"
