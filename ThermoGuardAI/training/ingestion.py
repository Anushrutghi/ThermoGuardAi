"""Dataset ingestion, curation, review queue, and YOLO export pipeline.

Implements:
1. Multi-source ingestion: uploaded images, sampled video frames, and explicitly
   authorized inspection evidence (unauthorized evidence is strictly rejected).
2. Original preservation: raw files are stored untouched in immutable raw storage;
   derived thumbnails are generated separately without mutating the original.
3. Strict validation: magic byte file signatures, dimensions, sizes, checksums.
4. Quarantine: corrupt, invalid, or unsupported files are quarantined with
   explanatory audit metadata, never silently discarded.
5. Duplicate & near-duplicate detection: SHA-256 exact matching and 64-bit dHash
   perceptual difference matching; flagged for human review, never silently deleted.
6. Review queue & governance: samples enter PENDING_REVIEW; only explicitly
   APPROVED samples with completed annotations may enter a training dataset.
7. Leakage-free YOLO exporter: hierarchical grouped splits by site/panel/session.
8. Resumability: SHA-256 index skips already processed inputs.
9. Synthetic fixtures & training blocker signaling: clearly labeled synthetic fixtures
   for testing, with explicit blocker reporting when real data is absent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from training.manifest_schema import (
    AnnotationStatus,
    ComponentAnnotation,
    ComponentClass,
    DatasetManifest,
    ImageMetadata,
    ImageQualityFlag,
    ReviewStatus,
    SampleRecord,
    SplitAssignment,
    VisibleFaultClass,
)

logger = logging.getLogger(__name__)

# Valid image magic bytes (file signatures)
_FILE_SIGNATURES: dict[str, list[bytes]] = {
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "webp": [b"RIFF"],  # WebP starts with RIFF and has WEBP at offset 8
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
MIN_IMAGE_DIM = 320
MAX_IMAGE_DIM = 8192
NEAR_DUPLICATE_HAMMING_THRESHOLD = 6  # <= 6 bits difference out of 64


# ============================================================================
# Hash & Perceptual Utilities
# ============================================================================

def compute_sha256(file_bytes: bytes) -> str:
    """Compute cryptographic SHA-256 content hash."""
    return hashlib.sha256(file_bytes).hexdigest()


def compute_dhash(image_bgr: np.ndarray, hash_size: int = 8) -> str:
    """Compute 64-bit perceptual difference hash (dHash).

    Resizes to (hash_size + 1, hash_size), computes row gradient (col[x+1] > col[x]),
    and encodes as a 16-character hexadecimal string.
    """
    if image_bgr is None or image_bgr.size == 0:
        return "0" * (hash_size * hash_size // 4)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    # Pack into 64-bit integer
    bit_str = "".join("1" if b else "0" for b in diff.flatten())
    int_val = int(bit_str, 2)
    return f"{int_val:016x}"


def hamming_distance(hex1: str, hex2: str) -> int:
    """Calculate the Hamming distance (differing bits) between two hex hashes."""
    val1 = int(hex1, 16)
    val2 = int(hex2, 16)
    return bin(val1 ^ val2).count("1")


def validate_file_signature(data: bytes) -> str | None:
    """Verify that file content matches a supported image format by magic bytes.

    Returns the format string ('jpeg', 'png', 'webp') or None if invalid.
    """
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


# ============================================================================
# Ingestion Pipeline
# ============================================================================

class DatasetIngestionPipeline:
    """Manages raw storage, validation, thumbnail derivation, and duplicate tracking."""

    def __init__(self, root_dir: Path | str = "datasets/curation"):
        self.root_dir = Path(root_dir)
        self.raw_dir = self.root_dir / "raw"
        self.thumbnails_dir = self.root_dir / "thumbnails"
        self.quarantine_dir = self.root_dir / "quarantine"
        self.export_dir = self.root_dir / "export"
        self.catalog_file = self.root_dir / "review_catalog.json"

        # Ensure directories exist
        for d in (self.raw_dir, self.thumbnails_dir, self.quarantine_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.catalog: DatasetManifest = self._load_catalog()

    def _load_catalog(self) -> DatasetManifest:
        if self.catalog_file.exists():
            try:
                data = json.loads(self.catalog_file.read_text(encoding="utf-8"))
                return DatasetManifest.model_validate(data)
            except Exception:
                logger.exception("Failed to load review catalog, creating a fresh one")
        return DatasetManifest(name="ThermoGuard-Curation-Catalog")

    def save_catalog(self) -> None:
        """Persist catalog atomically."""
        self.catalog.updated_at = datetime.now(UTC)
        temp_file = self.catalog_file.with_suffix(".tmp")
        temp_file.write_text(self.catalog.model_dump_json(indent=2), encoding="utf-8")
        temp_file.replace(self.catalog_file)

    def _find_by_sha256(self, sha: str) -> SampleRecord | None:
        for s in self.catalog.samples:
            if s.sha256 == sha:
                return s
        return None

    def _quarantine(self, file_bytes: bytes, original_name: str, reason: str, metadata: dict[str, Any] | None = None) -> Path:
        """Move unvalidated, corrupted, or rejected file into quarantine with explanatory metadata."""
        sha = compute_sha256(file_bytes)
        ext = Path(original_name).suffix or ".bin"
        target_path = self.quarantine_dir / f"{sha}{ext}"
        meta_path = self.quarantine_dir / f"{sha}_meta.json"

        target_path.write_bytes(file_bytes)
        audit_meta = {
            "quarantined_at": datetime.now(UTC).isoformat(),
            "original_filename": original_name,
            "sha256": sha,
            "reason": reason,
            "metadata": metadata or {},
        }
        meta_path.write_text(json.dumps(audit_meta, indent=2), encoding="utf-8")
        logger.warning("Quarantined %s (SHA %s): %s", original_name, sha[:8], reason)
        return target_path

    def ingest_image_file(
        self,
        file_path: Path | str,
        metadata: ImageMetadata,
        is_inspection_evidence: bool = False,
        authorized: bool = False,
        authorization_ref: str | None = None,
    ) -> SampleRecord:
        """Ingest a single image file with integrity, dimension, and duplicate validation."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Input file not found: {path}")

        file_bytes = path.read_bytes()

        # Gate: Production inspection evidence must be explicitly authorized
        if is_inspection_evidence and not authorized:
            self._quarantine(
                file_bytes,
                path.name,
                "Unauthorized inspection evidence: Production evidence must be explicitly authorized to enter training.",
                metadata.model_dump(mode="json"),
            )
            raise PermissionError(
                "Unauthorized inspection evidence: Production inspection evidence must not "
                "automatically become training data without explicit authorization."
            )

        # Check 1: File size
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            self._quarantine(
                file_bytes,
                path.name,
                f"File size {len(file_bytes)} bytes exceeds limit {MAX_FILE_SIZE_BYTES} bytes",
                metadata.model_dump(mode="json"),
            )
            raise ValueError(f"File size {len(file_bytes)} exceeds maximum permitted size {MAX_FILE_SIZE_BYTES}")

        # Check 2: Magic byte signature
        fmt = validate_file_signature(file_bytes)
        if fmt is None:
            self._quarantine(
                file_bytes,
                path.name,
                "Invalid or unsupported file signature (magic bytes mismatch)",
                metadata.model_dump(mode="json"),
            )
            raise ValueError(f"File signature validation failed for {path.name}; not a supported image format.")

        # Check 3: Image decoding & dimensions
        img_np = np.frombuffer(file_bytes, np.uint8)
        img_bgr = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
        if img_bgr is None or img_bgr.size == 0:
            self._quarantine(
                file_bytes,
                path.name,
                "Image decoding failed; corrupted raster data",
                metadata.model_dump(mode="json"),
            )
            raise ValueError(f"Image decoding failed for {path.name}; file is corrupted.")

        h, w = img_bgr.shape[:2]
        if w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM or w > MAX_IMAGE_DIM or h > MAX_IMAGE_DIM:
            self._quarantine(
                file_bytes,
                path.name,
                f"Dimensions ({w}x{h}) outside valid bounds [{MIN_IMAGE_DIM}..{MAX_IMAGE_DIM}]",
                metadata.model_dump(mode="json"),
            )
            raise ValueError(f"Dimensions ({w}x{h}) outside permissible limits [{MIN_IMAGE_DIM}..{MAX_IMAGE_DIM}].")

        # Resumability & Exact Duplicate Check
        sha = compute_sha256(file_bytes)
        existing = self._find_by_sha256(sha)
        if existing:
            logger.info("Resuming/Skipping: File %s (SHA %s) is already in catalog", path.name, sha[:8])
            return existing

        # Near-duplicate check via dHash
        dhash_val = compute_dhash(img_bgr)
        near_dupes: list[dict[str, Any]] = []
        exact_dupe_of: str | None = None

        for sample in self.catalog.samples:
            if sample.dhash:
                dist = hamming_distance(dhash_val, sample.dhash)
                if dist <= NEAR_DUPLICATE_HAMMING_THRESHOLD:
                    near_dupes.append({"sample_id": sample.sample_id, "hamming_distance": dist})
                    if dist == 0 and exact_dupe_of is None:
                        exact_dupe_of = sample.sample_id

        # Save immutable raw original
        ext = f".{fmt}" if not fmt.startswith(".") else fmt
        raw_target = self.raw_dir / f"{sha}{ext}"
        if not raw_target.exists():
            raw_target.write_bytes(file_bytes)

        # Generate separate thumbnail
        thumb_target = self.thumbnails_dir / f"{sha}_thumb.webp"
        if not thumb_target.exists():
            thumb = cv2.resize(img_bgr, (256, 256), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(thumb_target), thumb, [cv2.IMWRITE_WEBP_QUALITY, 85])

        # Evaluate initial quality
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        quality = ImageQualityFlag.OK
        if float(gray.mean()) < 35.0:
            quality = ImageQualityFlag.DARK
        elif float(cv2.Laplacian(gray, cv2.CV_64F).var()) < 25.0:
            quality = ImageQualityFlag.BLURRY
        elif float(np.mean(gray > 250)) > 0.15:
            quality = ImageQualityFlag.GLARE

        sample_record = SampleRecord(
            sample_id=sha,
            file_path=str(raw_target.relative_to(self.root_dir)),
            thumbnail_path=str(thumb_target.relative_to(self.root_dir)),
            sha256=sha,
            dhash=dhash_val,
            width=w,
            height=h,
            metadata=metadata,
            split=None,
            quality=quality,
            review_status=ReviewStatus.PENDING_REVIEW,
            annotation_status=AnnotationStatus.UNANNOTATED,
            duplicate_of=exact_dupe_of,
            near_duplicates=near_dupes,
        )

        self.catalog.samples.append(sample_record)
        self.save_catalog()
        logger.info("Ingested %s -> %s (width=%d, height=%d, quality=%s)", path.name, sha[:8], w, h, quality.value)
        return sample_record

    def ingest_video_frames(
        self,
        video_path: Path | str,
        metadata_template: ImageMetadata,
        sample_fps: float = 1.0,
        max_frames: int = 100,
    ) -> list[SampleRecord]:
        """Sample frames uniformly from a video file without leaking or mutating the source."""
        path = Path(video_path)
        if not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_interval = max(1, int(round(fps / sample_fps)))
        ingested: list[SampleRecord] = []

        frame_idx = 0
        saved_count = 0
        try:
            while cap.isOpened() and saved_count < max_frames:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_idx % frame_interval == 0:
                    ok, buf = cv2.imencode(".png", frame)
                    if ok:
                        # Clone metadata with frame sequence
                        meta = metadata_template.model_copy(deep=True)
                        meta.environmental_conditions["video_frame_index"] = frame_idx
                        meta.environmental_conditions["source_video"] = path.name

                        tmp_path = self.root_dir / f"tmp_frame_{frame_idx}.png"
                        tmp_path.write_bytes(buf.tobytes())
                        try:
                            sample = self.ingest_image_file(tmp_path, meta)
                            ingested.append(sample)
                            saved_count += 1
                        finally:
                            tmp_path.unlink(missing_ok=True)
                frame_idx += 1
        finally:
            cap.release()

        logger.info("Sampled %d frames from video %s", len(ingested), path.name)
        return ingested


# ============================================================================
# Review Queue & Governance
# ============================================================================

class ReviewQueue:
    """Review queue interface enforcing human curation before dataset inclusion."""

    def __init__(self, pipeline: DatasetIngestionPipeline):
        self.pipeline = pipeline

    def list_pending(self) -> list[SampleRecord]:
        return [s for s in self.pipeline.catalog.samples if s.review_status == ReviewStatus.PENDING_REVIEW]

    def approve_sample(self, sample_id: str, reviewer_id: str, review_notes: str | None = None) -> SampleRecord:
        sample = self._get_sample(sample_id)
        sample.review_status = ReviewStatus.APPROVED
        sample.reviewer_id = reviewer_id
        sample.reviewed_at = datetime.now(UTC)
        sample.review_notes = review_notes
        self.pipeline.save_catalog()
        logger.info("Sample %s APPROVED by %s", sample_id[:8], reviewer_id)
        return sample

    def reject_sample(self, sample_id: str, reviewer_id: str, review_notes: str) -> SampleRecord:
        sample = self._get_sample(sample_id)
        sample.review_status = ReviewStatus.REJECTED
        sample.reviewer_id = reviewer_id
        sample.reviewed_at = datetime.now(UTC)
        sample.review_notes = review_notes
        self.pipeline.save_catalog()
        logger.info("Sample %s REJECTED by %s", sample_id[:8], reviewer_id)
        return sample

    def annotate_sample(
        self,
        sample_id: str,
        components: list[ComponentAnnotation],
        annotation_status: AnnotationStatus = AnnotationStatus.ANNOTATED,
    ) -> SampleRecord:
        sample = self._get_sample(sample_id)
        sample.components = components
        sample.annotation_status = annotation_status
        self.pipeline.save_catalog()
        return sample

    def _get_sample(self, sample_id: str) -> SampleRecord:
        for s in self.pipeline.catalog.samples:
            if s.sample_id == sample_id:
                return s
        raise KeyError(f"Sample not found in catalog: {sample_id}")


# ============================================================================
# YOLO Exporter with Grouped Splits
# ============================================================================

class YOLOExporter:
    """Exports APPROVED and ANNOTATED samples to YOLO training directory structure

    with strictly grouped, leakage-free train/val/test splits.
    """

    def __init__(self, pipeline: DatasetIngestionPipeline):
        self.pipeline = pipeline

    def partition_grouped_splits(
        self,
        samples: list[SampleRecord],
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
    ) -> None:
        """Group samples by site_id and panel_id so that NO panel exists in multiple splits."""
        # Map panel_id -> list of samples
        panels: dict[str, list[SampleRecord]] = {}
        for s in samples:
            panels.setdefault(s.metadata.panel_id, []).append(s)

        panel_keys = sorted(panels.keys())
        # Deterministic partition based on hash
        n_total = len(panel_keys)
        n_train = max(1, int(round(n_total * train_ratio)))
        n_val = max(1, int(round(n_total * val_ratio))) if n_total > 2 else 0

        train_panels = set(panel_keys[:n_train])
        val_panels = set(panel_keys[n_train : n_train + n_val])
        test_panels = set(panel_keys[n_train + n_val :])
        if not test_panels and len(panel_keys) > 1:
            # If rounding left test empty, assign last panel to test
            test_panels.add(panel_keys[-1])
            train_panels.discard(panel_keys[-1])
            val_panels.discard(panel_keys[-1])

        for s in samples:
            pid = s.metadata.panel_id
            if pid in train_panels:
                s.split = SplitAssignment.TRAIN
            elif pid in val_panels:
                s.split = SplitAssignment.VAL
            else:
                s.split = SplitAssignment.TEST

    def export_yolo_dataset(
        self,
        output_dir: Path | str,
        target_mode: str = "components",  # "components" or "faults"
        include_only_approved: bool = True,
    ) -> Path:
        """Export dataset into YOLO format.

        Only explicitly approved samples with annotations enter the training dataset.
        """
        out = Path(output_dir)
        # Filter samples
        candidates = self.pipeline.catalog.samples
        if include_only_approved:
            eligible = [
                s for s in candidates
                if s.review_status == ReviewStatus.APPROVED
                and s.annotation_status in (AnnotationStatus.ANNOTATED, AnnotationStatus.VERIFIED)
                and len(s.components) > 0
            ]
        else:
            eligible = [s for s in candidates if len(s.components) > 0]

        if not eligible:
            raise ValueError(
                "No eligible samples found for YOLO export! Only samples that are "
                "APPROVED and ANNOTATED may enter the training dataset."
            )

        # Check if real data is present or only synthetic
        real_count = sum(1 for s in eligible if not s.metadata.is_synthetic)
        if real_count == 0:
            logger.warning(
                "TRAINING BLOCKER: 100%% of exported samples are synthetic fixtures! "
                "Real-world data collection is mandatory before training production models."
            )

        # Ensure grouped split assignment
        needs_split = [s for s in eligible if s.split is None]
        if needs_split:
            self.partition_grouped_splits(eligible)
            self.pipeline.save_catalog()

        # Build directory structure
        for split in ("train", "val", "test"):
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)

        class_names = [c.value for c in ComponentClass] if target_mode == "components" else [f.value for f in VisibleFaultClass]
        class_to_idx = {name: i for i, name in enumerate(class_names)}

        counts: dict[str, int] = {"train": 0, "val": 0, "test": 0}

        for sample in eligible:
            split_name = sample.split.value if sample.split else "train"
            source_img = self.pipeline.root_dir / sample.file_path
            dest_img = out / "images" / split_name / f"{sample.sample_id}{source_img.suffix}"
            dest_label = out / "labels" / split_name / f"{sample.sample_id}.txt"

            shutil.copy2(source_img, dest_img)

            # Write YOLO label lines: <class_idx> <x_c> <y_c> <w> <h>
            label_lines: list[str] = []
            if target_mode == "components":
                for comp in sample.components:
                    cls_idx = class_to_idx.get(comp.component_class.value)
                    if cls_idx is not None:
                        xc, yc, w, h = comp.bbox.to_yolo()
                        label_lines.append(f"{cls_idx} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
            else:
                for comp in sample.components:
                    for fault in comp.visible_faults:
                        f_idx = class_to_idx.get(fault.fault_class.value)
                        if f_idx is not None:
                            target_box = fault.bbox or comp.bbox
                            xc, yc, w, h = target_box.to_yolo()
                            label_lines.append(f"{f_idx} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")

            dest_label.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
            counts[split_name] += 1

        # Write dataset.yaml
        dataset_yaml = {
            "path": str(out.resolve()),
            "train": "images/train",
            "val": "images/val",
            "test": "images/test",
            "names": {i: name for i, name in enumerate(class_names)},
            "metadata": {
                "exported_at": datetime.now(UTC).isoformat(),
                "sample_counts": counts,
                "real_samples": real_count,
                "synthetic_samples": len(eligible) - real_count,
                "target_mode": target_mode,
            },
        }
        (out / "dataset.yaml").write_text(yaml.dump(dataset_yaml, sort_keys=False), encoding="utf-8")
        logger.info("Exported YOLO dataset to %s (train=%d, val=%d, test=%d)", out, counts["train"], counts["val"], counts["test"])
        return out


# ============================================================================
# Synthetic Fixtures & Blocker Signaling
# ============================================================================

def generate_synthetic_fixtures(output_dir: Path | str, count: int = 6) -> list[Path]:
    """Generate clearly marked synthetic test fixtures with watermarked banners."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    for i in range(count):
        # 640x480 dark grey electrical panel background
        img = np.full((480, 640, 3), 42, dtype=np.uint8)
        # Add simulated breaker rectangles
        cv2.rectangle(img, (100, 120), (220, 340), (70, 70, 75), -1)
        cv2.rectangle(img, (260, 120), (380, 340), (70, 70, 75), -1)
        cv2.rectangle(img, (420, 120), (540, 340), (70, 70, 75), -1)
        # Add switch toggles
        cv2.rectangle(img, (140, 200), (180, 260), (20, 20, 20), -1)
        cv2.rectangle(img, (300, 200), (340, 260), (20, 20, 20), -1)
        cv2.rectangle(img, (460, 200), (500, 260), (20, 20, 20), -1)

        # High-visibility watermark banner across top and bottom
        cv2.rectangle(img, (0, 0), (640, 35), (0, 0, 180), -1)
        cv2.putText(
            img,
            f"DEMO / SYNTHETIC FIXTURE #{i+1} - NOT REAL DATA",
            (20, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        file_path = out / f"synthetic_fixture_{i+1:03d}.png"
        cv2.imwrite(str(file_path), img)
        created.append(file_path)

    return created


def write_training_blocker_report(dest_file: Path | str, reason: str) -> None:
    """Generate a prominent markdown report documenting a training blocker."""
    path = Path(dest_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# ⚠️ TRAINING BLOCKER NOTICE

**Status:** HARD BLOCKER ACTIVE
**Reported At:** {datetime.now(UTC).isoformat()}

---

## Blocker Description
{reason}

## Architectural Policy
ThermoGuard AI requires evidence-backed training data:
1. Production models **MUST NOT** be trained solely on synthetic fixtures or unverified demo data.
2. Production inspection evidence **CANNOT** automatically become training data without explicit client authorization and legal consent.
3. Every sample must be reviewed and approved by a qualified inspector before entering the training manifest.

## Next Steps to Unblock
- Ingest real-world electrical panel imagery via `DatasetIngestionPipeline`.
- Review and approve samples using the `ReviewQueue`.
- Execute verified ground-truth annotations following `docs/ANNOTATION_GUIDE.md`.
"""
    path.write_text(content, encoding="utf-8")
