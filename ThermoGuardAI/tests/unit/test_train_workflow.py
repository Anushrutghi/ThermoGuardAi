"""Unit tests for the reproducible training workflow, compute checks, and licensing audit."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
import yaml

from training.train_yolo import (
    check_compute_environment,
    get_dependency_and_licensing_audit,
    train,
)


@pytest.fixture
def synthetic_dataset_fixture(tmp_path: Path) -> Path:
    """Create a minimal synthetic dataset fixture."""
    ds_root = tmp_path / "synthetic_dataset"
    (ds_root / "images" / "train").mkdir(parents=True)
    (ds_root / "images" / "val").mkdir(parents=True)
    (ds_root / "labels" / "train").mkdir(parents=True)
    (ds_root / "labels" / "val").mkdir(parents=True)

    img_train = np.full((480, 640, 3), 100, dtype=np.uint8)
    cv2.imwrite(str(ds_root / "images" / "train" / "train_01.png"), img_train)
    (ds_root / "labels" / "train" / "train_01.txt").write_text("0 0.5 0.5 0.2 0.3\n")

    img_val = np.full((480, 640, 3), 150, dtype=np.uint8)
    cv2.imwrite(str(ds_root / "images" / "val" / "val_01.png"), img_val)
    (ds_root / "labels" / "val" / "val_01.txt").write_text("0 0.5 0.5 0.2 0.3\n")

    cfg = {
        "path": str(ds_root),
        "train": "images/train",
        "val": "images/val",
        "names": {0: "circuit_breaker"},
        "metadata": {"real_samples": 0, "synthetic_samples": 2},
    }
    yaml_file = ds_root / "dataset.yaml"
    yaml_file.write_text(yaml.dump(cfg))
    return yaml_file


class TestComputeAndLicensingAudit:
    """Verify hardware environment checks and licensing documentation."""

    def test_compute_environment_detection(self):
        info = check_compute_environment(requested_device="auto")
        assert "platform" in info
        assert "ram_total_gb" in info
        assert "resolved_device" in info
        assert info["ram_total_gb"] > 0
        assert info["cpu_count_logical"] >= 1

    def test_licensing_considerations_documented(self):
        audit = get_dependency_and_licensing_audit()
        assert "dependencies" in audit
        assert "licensing" in audit
        # Verify Ultralytics AGPL-3.0 and ONNX export remedy are documented
        ultralytics_license = audit["licensing"]["ultralytics"]
        assert ultralytics_license["license"] == "AGPL-3.0"
        assert "Commercial License" in ultralytics_license["deployment_implication"]
        assert "Export to ONNX" in ultralytics_license["export_remedy"]


class TestTrainingPreFlightBlockers:
    """Verify that training blocks on invalid datasets or unreviewed synthetic fixtures."""

    def test_block_on_invalid_dataset(self, tmp_path):
        bad_yaml = tmp_path / "bad_dataset.yaml"
        bad_yaml.write_text("invalid_content: true")

        with pytest.raises(RuntimeError) as exc:
            train(data=str(bad_yaml), model="yolo11n.pt")
        assert "Dataset validation failed" in str(exc.value)

    def test_block_on_synthetic_only_dataset(self, synthetic_dataset_fixture):
        """Training on synthetic fixtures without --allow-synthetic or --smoke-test must be blocked."""
        with pytest.raises(RuntimeError) as exc:
            train(
                data=str(synthetic_dataset_fixture),
                model="yolo11n.pt",
                allow_synthetic_only=False,
                smoke_test=False,
            )
        assert "TRAINING BLOCKER: The dataset contains solely synthetic fixtures" in str(exc.value)

    @patch("training.train_yolo.benchmark_inference_latency")
    def test_smoke_test_mode_generates_provenance_and_report(self, mock_latency, synthetic_dataset_fixture, tmp_path):
        """Smoke-test mode allows running synthetic fixtures and produces full audit artifacts."""
        mock_latency.return_value = {
            "mean_ms": 12.5,
            "median_p50_ms": 12.0,
            "p95_ms": 15.0,
            "p99_ms": 18.0,
            "fps_estimate": 80.0,
        }

        out_dir = tmp_path / "smoke_run"

        # Mock ultralytics YOLO to avoid invoking full PyTorch weights training in unit test
        mock_yolo_instance = MagicMock()
        mock_weights_dir = out_dir / "weights"
        mock_weights_dir.mkdir(parents=True, exist_ok=True)
        (mock_weights_dir / "best.pt").write_bytes(b"dummy_pt_weights")

        with patch("ultralytics.YOLO", return_value=mock_yolo_instance):
            provenance = train(
                data=str(synthetic_dataset_fixture),
                model="yolo11n.pt",
                epochs=1,
                batch=2,
                smoke_test=True,
                allow_synthetic_only=True,
                output_dir=str(out_dir),
            )

        assert provenance["smoke_test"] is True
        assert provenance["is_synthetic_dataset"] is True
        assert (out_dir / "run_provenance.json").exists()
        assert (out_dir / "evaluation_report.md").exists()

        report_text = (out_dir / "evaluation_report.md").read_text()
        assert "DEPLOYMENT BLOCKER ACTIVE" in report_text
        assert "Licensing & Deployment Guidance" in report_text
