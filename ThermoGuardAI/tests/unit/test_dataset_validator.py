"""Unit tests for DatasetValidator with deliberately corrupted and leaking dataset fixtures."""
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from training.validate_dataset import DatasetValidator


@pytest.fixture
def base_dataset(tmp_path: Path) -> Path:
    """Create a minimal valid YOLO dataset structure with 1 train and 1 val image."""
    ds_root = tmp_path / "valid_dataset"
    (ds_root / "images" / "train").mkdir(parents=True)
    (ds_root / "images" / "val").mkdir(parents=True)
    (ds_root / "images" / "test").mkdir(parents=True)
    (ds_root / "labels" / "train").mkdir(parents=True)
    (ds_root / "labels" / "val").mkdir(parents=True)
    (ds_root / "labels" / "test").mkdir(parents=True)

    # Valid image & label for train
    img_train = np.full((480, 640, 3), 100, dtype=np.uint8)
    cv2.imwrite(str(ds_root / "images" / "train" / "train_001.png"), img_train)
    (ds_root / "labels" / "train" / "train_001.txt").write_text("0 0.5 0.5 0.2 0.3\n")

    # Valid image & label for val
    img_val = np.full((480, 640, 3), 110, dtype=np.uint8)
    cv2.imwrite(str(ds_root / "images" / "val" / "val_001.png"), img_val)
    (ds_root / "labels" / "val" / "val_001.txt").write_text("1 0.4 0.4 0.3 0.2\n")

    # Valid image & label for test
    img_test = np.full((480, 640, 3), 120, dtype=np.uint8)
    cv2.imwrite(str(ds_root / "images" / "test" / "test_001.png"), img_test)
    (ds_root / "labels" / "test" / "test_001.txt").write_text("0 0.3 0.3 0.1 0.2\n")

    # dataset.yaml
    yaml_content = {
        "path": str(ds_root),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {0: "circuit_breaker", 1: "fuse"},
    }
    yaml_file = ds_root / "dataset.yaml"
    yaml_file.write_text(yaml.dump(yaml_content))
    return yaml_file


class TestDatasetValidatorIntegrity:
    """Verify detection of file corruption, malformed labels, and out-of-bounds geometry."""

    def test_valid_dataset_passes(self, base_dataset):
        validator = DatasetValidator(base_dataset)
        assert validator.validate() is True
        assert len(validator.errors) == 0
        assert (validator.output_dir / "quality_report.json").exists()
        assert (validator.output_dir / "quality_report.md").exists()
        assert (validator.output_dir / "dataset_manifest.json").exists()

    def test_missing_dataset_yaml(self, tmp_path):
        validator = DatasetValidator(tmp_path / "nonexistent.yaml")
        assert validator.validate() is False
        assert any("not found" in err for err in validator.errors)

    def test_corrupted_image_detected(self, base_dataset):
        root = base_dataset.parent
        bad_img = root / "images" / "train" / "corrupted_002.png"
        bad_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)  # valid magic bytes but corrupt payload

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("Corrupt image; OpenCV could not decode" in err for err in validator.errors)

    def test_empty_image_detected(self, base_dataset):
        root = base_dataset.parent
        empty_img = root / "images" / "train" / "empty_003.png"
        empty_img.write_bytes(b"")  # 0 bytes

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("Empty image file (0 bytes)" in err for err in validator.errors)

    def test_malformed_label_token_count(self, base_dataset):
        root = base_dataset.parent
        # Add 4-token line (missing height)
        lbl_file = root / "labels" / "train" / "train_001.txt"
        lbl_file.write_text("0 0.5 0.5 0.2\n")

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("expected 5 tokens" in err for err in validator.errors)

    def test_invalid_class_id(self, base_dataset):
        root = base_dataset.parent
        # Class 99 when only classes 0 and 1 are defined
        lbl_file = root / "labels" / "train" / "train_001.txt"
        lbl_file.write_text("99 0.5 0.5 0.2 0.3\n")

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("Invalid class ID 99" in err for err in validator.errors)

    def test_out_of_bounds_box_center(self, base_dataset):
        root = base_dataset.parent
        # Center x = 1.5 (> 1.0)
        lbl_file = root / "labels" / "train" / "train_001.txt"
        lbl_file.write_text("0 1.5 0.5 0.2 0.3\n")

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("Out-of-bounds center (1.5000" in err for err in validator.errors)

    def test_box_extending_beyond_frame(self, base_dataset):
        root = base_dataset.parent
        # Box centered at 0.9 with width 0.4 (extends to 1.1)
        lbl_file = root / "labels" / "train" / "train_001.txt"
        lbl_file.write_text("0 0.9 0.5 0.4 0.3\n")

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("extends beyond frame bounds" in err for err in validator.errors)


class TestDatasetValidatorLeakageAndGates:
    """Verify split leakage detection, test split isolation, and imbalance gates."""

    def test_duplicate_image_leakage_detected(self, base_dataset):
        root = base_dataset.parent
        train_img = root / "images" / "train" / "train_001.png"
        test_img = root / "images" / "test" / "test_duplicate.png"
        # Copy exact same image bytes to test split
        test_img.write_bytes(train_img.read_bytes())
        (root / "labels" / "test" / "test_duplicate.txt").write_text("0 0.5 0.5 0.2 0.3\n")

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("Duplicate image leakage detected!" in err for err in validator.errors)

    def test_group_leakage_detected(self, base_dataset):
        root = base_dataset.parent
        # Rename train image to PNL1_frame1.png
        pnl1_train = root / "images" / "train" / "PNL1_frame1.png"
        (root / "images" / "train" / "train_001.png").rename(pnl1_train)
        (root / "labels" / "train" / "train_001.txt").rename(root / "labels" / "train" / "PNL1_frame1.txt")

        # Create different image in test with same panel prefix PNL1_frame2.png
        img2 = np.full((480, 640, 3), 150, dtype=np.uint8)
        pnl1_test = root / "images" / "test" / "PNL1_frame2.png"
        cv2.imwrite(str(pnl1_test), img2)
        (root / "labels" / "test" / "PNL1_frame2.txt").write_text("0 0.4 0.4 0.2 0.2\n")

        validator = DatasetValidator(base_dataset)
        assert validator.validate() is False
        assert any("Group leakage detected! Panel(s) {'PNL1'}" in err for err in validator.errors)

    def test_class_imbalance_warning_vs_gate(self, base_dataset):
        root = base_dataset.parent
        # Create severe imbalance: 50 class 0, 1 class 1
        lines = ["0 0.5 0.5 0.1 0.1\n"] * 60
        (root / "labels" / "train" / "train_001.txt").write_text("".join(lines))

        # Warning by default (validate returns True)
        v_warn = DatasetValidator(base_dataset, fail_on_imbalance=False, max_imbalance_ratio=10.0)
        assert v_warn.validate() is True
        assert any("Severe class imbalance" in w for w in v_warn.warnings)

        # Gate tripped when fail_on_imbalance=True (validate returns False)
        v_gate = DatasetValidator(base_dataset, fail_on_imbalance=True, max_imbalance_ratio=10.0)
        assert v_gate.validate() is False
        assert any("Severe class imbalance" in err for err in v_gate.errors)
