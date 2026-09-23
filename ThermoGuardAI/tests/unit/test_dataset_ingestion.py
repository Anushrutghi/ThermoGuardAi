"""Unit tests for dataset ingestion, validation, quarantine, review queue, and YOLO export."""
import json
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from training.ingestion import (
    DatasetIngestionPipeline,
    ReviewQueue,
    YOLOExporter,
    compute_dhash,
    compute_sha256,
    generate_synthetic_fixtures,
    hamming_distance,
    validate_file_signature,
    write_training_blocker_report,
)
from training.manifest_schema import (
    AnnotationBoundingBox,
    AnnotationStatus,
    ComponentAnnotation,
    ComponentClass,
    ImageMetadata,
    ReviewStatus,
)


@pytest.fixture
def temp_curation_dir(tmp_path: Path) -> Path:
    return tmp_path / "dataset_curation"


@pytest.fixture
def sample_image_file(tmp_path: Path) -> Path:
    """Create a valid 640x480 test image."""
    img = np.full((480, 640, 3), 100, dtype=np.uint8)
    cv2.circle(img, (320, 240), 50, (0, 0, 255), -1)
    file_path = tmp_path / "valid_breaker_01.png"
    cv2.imwrite(str(file_path), img)
    return file_path


@pytest.fixture
def sample_metadata() -> ImageMetadata:
    return ImageMetadata(
        site_id="SITE-EAST-01",
        panel_id="PANEL-FEEDER-A",
        session_id="SESS-20260923-01",
        camera_model="Sony-Alpha-6700",
        timestamp=datetime.now(UTC),
        data_rights="internal_inspection",
    )


class TestFileSignaturesAndHashing:
    """Verify magic bytes and perceptual hashing algorithms."""

    def test_file_signature_validation(self):
        assert validate_file_signature(b"\xff\xd8\xff\xe0" + b"\x00" * 16) == "jpeg"
        assert validate_file_signature(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == "png"
        assert validate_file_signature(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16) == "webp"
        assert validate_file_signature(b"NOT_AN_IMAGE_FILE_DATA") is None

    def test_perceptual_dhash_and_distance(self):
        img1 = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.rectangle(img1, (100, 100), (300, 300), (255, 255, 255), -1)

        # Identical image -> distance 0
        h1 = compute_dhash(img1)
        h2 = compute_dhash(img1.copy())
        assert hamming_distance(h1, h2) == 0

        # Minor shift/noise -> small distance
        img_noisy = img1.copy()
        img_noisy[100:105, 100:105] = 128
        h_noisy = compute_dhash(img_noisy)
        assert hamming_distance(h1, h_noisy) <= 4


class TestDatasetIngestionPipeline:
    """Verify ingestion, validation, quarantine, and duplicate handling."""

    def test_ingest_valid_image(self, temp_curation_dir, sample_image_file, sample_metadata):
        pipeline = DatasetIngestionPipeline(temp_curation_dir)
        record = pipeline.ingest_image_file(sample_image_file, sample_metadata)

        assert record.sha256 == compute_sha256(sample_image_file.read_bytes())
        assert record.width == 640
        assert record.height == 480
        assert record.review_status == ReviewStatus.PENDING_REVIEW
        assert record.annotation_status == AnnotationStatus.UNANNOTATED

        # Check raw original exists
        raw_path = temp_curation_dir / record.file_path
        assert raw_path.exists()
        assert raw_path.read_bytes() == sample_image_file.read_bytes()

        # Check derived thumbnail exists
        thumb_path = temp_curation_dir / record.thumbnail_path
        assert thumb_path.exists()

    def test_quarantine_corrupted_file(self, temp_curation_dir, sample_metadata, tmp_path):
        bad_file = tmp_path / "corrupted.jpg"
        bad_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)

        pipeline = DatasetIngestionPipeline(temp_curation_dir)
        with pytest.raises(ValueError) as exc:
            pipeline.ingest_image_file(bad_file, sample_metadata)
        assert "Image decoding failed" in str(exc.value)

        # Verify file and audit json moved to quarantine
        quarantine_files = list(pipeline.quarantine_dir.glob("*.jpg"))
        assert len(quarantine_files) == 1
        meta_files = list(pipeline.quarantine_dir.glob("*_meta.json"))
        assert len(meta_files) == 1
        audit = json.loads(meta_files[0].read_text())
        assert "Image decoding failed" in audit["reason"]

    def test_quarantine_unsupported_signature(self, temp_curation_dir, sample_metadata, tmp_path):
        txt_file = tmp_path / "script.py"
        txt_file.write_text("print('hello world')")

        pipeline = DatasetIngestionPipeline(temp_curation_dir)
        with pytest.raises(ValueError) as exc:
            pipeline.ingest_image_file(txt_file, sample_metadata)
        assert "File signature validation failed" in str(exc.value)
        assert len(list(pipeline.quarantine_dir.glob("*.py"))) == 1

    def test_inspection_evidence_authorization_gate(self, temp_curation_dir, sample_image_file, sample_metadata):
        """Production inspection evidence must not become training data without authorization."""
        pipeline = DatasetIngestionPipeline(temp_curation_dir)

        # Unauthorized attempt -> strictly blocked & quarantined
        with pytest.raises(PermissionError) as exc:
            pipeline.ingest_image_file(
                sample_image_file,
                sample_metadata,
                is_inspection_evidence=True,
                authorized=False,
            )
        assert "Production inspection evidence must not automatically become training data" in str(exc.value)

        # Authorized attempt -> succeeds
        record = pipeline.ingest_image_file(
            sample_image_file,
            sample_metadata,
            is_inspection_evidence=True,
            authorized=True,
            authorization_ref="TICKET-AUTH-4412",
        )
        assert record.review_status == ReviewStatus.PENDING_REVIEW

    def test_duplicate_and_near_duplicate_detection(self, temp_curation_dir, sample_image_file, sample_metadata, tmp_path):
        pipeline = DatasetIngestionPipeline(temp_curation_dir)

        # Ingest first
        rec1 = pipeline.ingest_image_file(sample_image_file, sample_metadata)

        # Ingest exact same file -> resumes / returns rec1
        rec1_again = pipeline.ingest_image_file(sample_image_file, sample_metadata)
        assert rec1_again.sample_id == rec1.sample_id
        assert len(pipeline.catalog.samples) == 1

        # Create near-duplicate (slightly different background pixel)
        img = cv2.imread(str(sample_image_file))
        img[0, 0] = [102, 100, 100]
        near_file = tmp_path / "near_dupe.png"
        cv2.imwrite(str(near_file), img)

        meta2 = sample_metadata.model_copy()
        meta2.session_id = "SESS-2"
        rec2 = pipeline.ingest_image_file(near_file, meta2)
        assert len(pipeline.catalog.samples) == 2
        # Near duplicate must be flagged, NOT deleted
        assert len(rec2.near_duplicates) >= 1
        assert rec2.near_duplicates[0]["sample_id"] == rec1.sample_id


class TestReviewQueueAndExport:
    """Verify human review gating and YOLO dataset export."""

    def test_review_queue_lifecycle(self, temp_curation_dir, sample_image_file, sample_metadata):
        pipeline = DatasetIngestionPipeline(temp_curation_dir)
        sample = pipeline.ingest_image_file(sample_image_file, sample_metadata)

        queue = ReviewQueue(pipeline)
        assert len(queue.list_pending()) == 1

        # Annotate
        comp = ComponentAnnotation(
            component_class=ComponentClass.CIRCUIT_BREAKER,
            component_id="B1",
            bbox=AnnotationBoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.8),
        )
        queue.annotate_sample(sample.sample_id, [comp], AnnotationStatus.ANNOTATED)

        # Approve
        approved = queue.approve_sample(sample.sample_id, reviewer_id="inspector_dan", review_notes="Clean breaker box")
        assert approved.review_status == ReviewStatus.APPROVED
        assert approved.reviewer_id == "inspector_dan"

    def test_yolo_export_enforces_approval(self, temp_curation_dir, sample_image_file, sample_metadata, tmp_path):
        pipeline = DatasetIngestionPipeline(temp_curation_dir)
        sample = pipeline.ingest_image_file(sample_image_file, sample_metadata)
        exporter = YOLOExporter(pipeline)
        export_target = tmp_path / "yolo_export"

        # Attempt export before approval -> raises ValueError
        with pytest.raises(ValueError) as exc:
            exporter.export_yolo_dataset(export_target, include_only_approved=True)
        assert "No eligible samples found for YOLO export" in str(exc.value)

        # Annotate and Approve sample
        queue = ReviewQueue(pipeline)
        queue.annotate_sample(
            sample.sample_id,
            [
                ComponentAnnotation(
                    component_class=ComponentClass.CIRCUIT_BREAKER,
                    bbox=AnnotationBoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.8),
                )
            ],
            AnnotationStatus.ANNOTATED,
        )
        queue.approve_sample(sample.sample_id, "inspector_dan")

        # Now export succeeds
        out = exporter.export_yolo_dataset(export_target, include_only_approved=True)
        assert (out / "dataset.yaml").exists()
        assert (out / "images" / "train").exists()
        assert (out / "labels" / "train").exists()


class TestSyntheticFixturesAndBlocker:
    """Verify synthetic fixture generation and blocker reporting."""

    def test_generate_synthetic_fixtures(self, tmp_path):
        out_dir = tmp_path / "fixtures"
        fixtures = generate_synthetic_fixtures(out_dir, count=3)
        assert len(fixtures) == 3
        for f in fixtures:
            assert f.exists()
            img = cv2.imread(str(f))
            assert img.shape == (480, 640, 3)

    def test_write_training_blocker_report(self, tmp_path):
        report_file = tmp_path / "TRAINING_BLOCKER.md"
        write_training_blocker_report(report_file, "Zero real-world electrical images annotated.")
        assert report_file.exists()
        text = report_file.read_text()
        assert "TRAINING BLOCKER NOTICE" in text
        assert "Zero real-world electrical images annotated" in text
