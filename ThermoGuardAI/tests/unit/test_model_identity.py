"""S0 honesty guarantees: model identity + simulated-thermal labeling."""

import numpy as np

from ai.detector.yolo_detector import is_electrical_model
from ai.thermal.simulator import ThermalSimulator


# ---------------------------------------------------------------------------
# YOLO model identity — a generic COCO model must never pass as electrical
# ---------------------------------------------------------------------------
def test_coco_model_rejected() -> None:
    """A model whose classes are everyday COCO objects is not electrical."""
    coco = {i: n for i, n in enumerate(["person", "bicycle", "car", "dog", "cat", "tv", "bottle", "cup"])}
    ok, reason = is_electrical_model(coco)
    assert ok is False
    assert "electrical" in reason


def test_electrical_model_accepted() -> None:
    names = {0: "circuit_breaker", 1: "fuse", 2: "relay", 3: "contactor", 4: "busbar", 5: "terminal", 6: "cable"}
    ok, reason = is_electrical_model(names)
    assert ok is True
    assert reason == "electrical model"


def test_mixed_model_rejected_without_enough_overlap() -> None:
    """A model with only 1–2 coincidental matches is still not electrical."""
    names = {0: "circuit_breaker", 1: "person", 2: "dog", 3: "car"}
    ok, _ = is_electrical_model(names)
    assert ok is False


def test_empty_or_missing_names_rejected() -> None:
    assert is_electrical_model({})[0] is False
    assert is_electrical_model(None)[0] is False


# ---------------------------------------------------------------------------
# Thermal honesty — simulated sources must be detectable by the UI layer
# ---------------------------------------------------------------------------
def test_simulator_marked_as_simulated() -> None:
    assert ThermalSimulator().simulated is True


def test_pipeline_json_reports_simulated_thermal() -> None:
    """The live result payload must carry thermal_source + thermal_simulated so
    dashboards can label simulated readings DEMO (never present them as real)."""
    from ai.pipeline import InspectionPipeline

    pipe = InspectionPipeline()
    result = pipe.process(np.zeros((240, 320, 3), dtype=np.uint8))
    data = result.to_json()
    assert data["thermal_simulated"] is True
    assert data["thermal_source"] == "simulator"
