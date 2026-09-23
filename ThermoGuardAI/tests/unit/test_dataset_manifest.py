"""Unit tests for dataset manifest schema, verification rules, and grouped split validators."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from training.manifest_schema import (
    AnnotationBoundingBox,
    AnnotationStatus,
    ComponentAnnotation,
    ComponentClass,
    DatasetManifest,
    HardNegativeAnnotation,
    HardNegativeType,
    ImageMetadata,
    ReviewStatus,
    SampleRecord,
    SplitAssignment,
    VerificationSource,
    VisibleFaultAnnotation,
    VisibleFaultClass,
)


class TestAnnotationBoundingBox:
    """Bounding box coordinates and YOLO normalization tests."""

    def test_valid_box_and_yolo_conversion(self):
        box = AnnotationBoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6)
        xc, yc, w, h = box.to_yolo()
        assert xc == 0.3
        assert yc == 0.4
        assert w == 0.4
        assert h == 0.4

    def test_invalid_box_inverted_coordinates(self):
        with pytest.raises(ValidationError) as exc:
            AnnotationBoundingBox(x_min=0.6, y_min=0.2, x_max=0.3, y_max=0.6)
        assert "x_min (0.6) must be strictly less than x_max (0.3)" in str(exc.value)

    def test_out_of_bounds_coordinates(self):
        with pytest.raises(ValidationError):
            AnnotationBoundingBox(x_min=-0.1, y_min=0.0, x_max=0.5, y_max=0.5)
        with pytest.raises(ValidationError):
            AnnotationBoundingBox(x_min=0.1, y_min=0.0, x_max=1.2, y_max=0.5)


class TestLooseConnectionVerificationRule:
    """Enforce rule: loose connection cannot be labeled from visual review alone."""

    def test_reject_loose_connection_from_visual_review(self):
        """Visual inspection alone is insufficient to declare an electrical connection loose."""
        with pytest.raises(ValidationError) as exc:
            VisibleFaultAnnotation(
                fault_class=VisibleFaultClass.VERIFIED_LOOSE_CONNECTION,
                verification_source=VerificationSource.VISUAL_EXPERT_REVIEW,
                verification_notes="Looked loose in the photo",
            )
        assert "CANNOT be labeled loose solely from an ordinary RGB visual image" in str(exc.value)

    def test_reject_loose_connection_without_notes(self):
        """Even with torque test, detailed verification report notes are mandatory."""
        with pytest.raises(ValidationError) as exc:
            VisibleFaultAnnotation(
                fault_class=VisibleFaultClass.VERIFIED_LOOSE_CONNECTION,
                verification_source=VerificationSource.CALIBRATED_TORQUE_TEST,
                verification_notes="",
            )
        assert "requires non-empty verification_notes" in str(exc.value)

    def test_accept_verified_loose_connection_with_torque_test(self):
        """Calibrated torque test with measured values satisfies verification protocol."""
        fault = VisibleFaultAnnotation(
            fault_class=VisibleFaultClass.VERIFIED_LOOSE_CONNECTION,
            verification_source=VerificationSource.CALIBRATED_TORQUE_TEST,
            verification_notes="Terminal screw broke loose at 0.8 N·m (specified torque is 2.5 N·m).",
        )
        assert fault.fault_class == VisibleFaultClass.VERIFIED_LOOSE_CONNECTION
        assert fault.verification_source == VerificationSource.CALIBRATED_TORQUE_TEST

    def test_accept_verified_loose_connection_with_micro_ohmmeter(self):
        """4-wire contact resistance test satisfies verification protocol."""
        fault = VisibleFaultAnnotation(
            fault_class=VisibleFaultClass.VERIFIED_LOOSE_CONNECTION,
            verification_source=VerificationSource.MICRO_OHMMETER_TEST,
            verification_notes="DLRO measured 14.2 mΩ across lug vs 0.12 mΩ on adjacent phase.",
        )
        assert fault.verification_source == VerificationSource.MICRO_OHMMETER_TEST


class TestComponentMultiLabelFaults:
    """Support multiple visible fault labels on a single component instance."""

    def test_component_with_multiple_faults(self):
        comp = ComponentAnnotation(
            component_class=ComponentClass.CIRCUIT_BREAKER,
            component_id="B1",
            bbox=AnnotationBoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.5),
            visible_faults=[
                VisibleFaultAnnotation(
                    fault_class=VisibleFaultClass.SURFACE_CORROSION_OXIDATION,
                    verification_notes="Rust on steel terminal screw head",
                ),
                VisibleFaultAnnotation(
                    fault_class=VisibleFaultClass.EXPOSED_UNINSULATED_CONDUCTOR,
                    verification_notes="12 mm bare copper conductor protruding beyond terminal throat",
                ),
            ],
        )
        assert len(comp.visible_faults) == 2
        assert comp.is_healthy is False  # automatically synced


class TestHardNegatives:
    """Test representation of benign artifacts that mimic faults."""

    def test_hard_negative_marking(self):
        hn = HardNegativeAnnotation(
            negative_type=HardNegativeType.MANUFACTURING_MARKING,
            bbox=AnnotationBoundingBox(x_min=0.4, y_min=0.4, x_max=0.45, y_max=0.45),
            mimicked_fault=VisibleFaultClass.SCORCHING_CHARRING,
            notes="Factory black ink date code stamp '2023-W42' on plastic body",
        )
        assert hn.negative_type == HardNegativeType.MANUFACTURING_MARKING
        assert hn.mimicked_fault == VisibleFaultClass.SCORCHING_CHARRING


class TestGroupedSplitLeakageDetection:
    """Verify that images of the same panel or session cannot leak across train/val/test."""

    def _sample(self, sample_id: str, panel_id: str, session_id: str, split: SplitAssignment) -> SampleRecord:
        return SampleRecord(
            sample_id=sample_id,
            file_path=f"raw/{sample_id}.jpg",
            sha256=sample_id,
            width=640,
            height=480,
            metadata=ImageMetadata(
                site_id="SITE-01",
                panel_id=panel_id,
                session_id=session_id,
                timestamp=datetime.now(UTC),
            ),
            split=split,
            review_status=ReviewStatus.APPROVED,
            annotation_status=AnnotationStatus.ANNOTATED,
        )

    def test_valid_grouped_splits(self):
        """Distinct panels across train and test must pass validation."""
        s1 = self._sample("sha1", "PANEL_A", "SESS_1", SplitAssignment.TRAIN)
        s2 = self._sample("sha2", "PANEL_A", "SESS_1", SplitAssignment.TRAIN)
        s3 = self._sample("sha3", "PANEL_B", "SESS_2", SplitAssignment.VAL)
        s4 = self._sample("sha4", "PANEL_C", "SESS_3", SplitAssignment.TEST)

        manifest = DatasetManifest(name="Valid-Grouped", samples=[s1, s2, s3, s4])
        assert len(manifest.samples) == 4

    def test_reject_panel_leakage_across_splits(self):
        """The same physical panel across train and test must trigger a leakage error."""
        s1 = self._sample("sha1", "PANEL_LEAK", "SESS_1", SplitAssignment.TRAIN)
        s2 = self._sample("sha2", "PANEL_LEAK", "SESS_2", SplitAssignment.TEST)  # same panel in test!

        with pytest.raises(ValidationError) as exc:
            DatasetManifest(name="Leaking-Dataset", samples=[s1, s2])
        assert "Data leakage detected! The following panel_ids are assigned to multiple splits" in str(exc.value)
        assert "PANEL_LEAK" in str(exc.value)
