"""YOLO detector using Ultralytics. Falls back gracefully when the model file is missing."""
from __future__ import annotations

import logging

import numpy as np

from ai.detector.base import CLASS_NAMES, COMPONENT_CLASSES, BaseDetector, Detection
from backend.core.config import get_settings

logger = logging.getLogger(__name__)


def is_electrical_model(names: dict) -> tuple[bool, str]:
    """Check whether a YOLO model's class names match the electrical set.

    The bundled ``electrical_yolo.pt`` is a copy of the generic COCO yolo11n
    weights (80 everyday objects: person, car, dog…). Running it through our
    electrical class mapping would silently mislabel everyday objects as
    circuit breakers — i.e. fake AI results. A model is therefore only accepted
    when its class names genuinely overlap the platform's electrical classes.
    """
    if not names:
        return False, "model has no class names"
    actual = {str(v).strip().lower() for v in names.values()}
    expected = {c.lower() for c in COMPONENT_CLASSES}
    overlap = sorted(actual & expected)
    if len(overlap) < 3:
        sample = ", ".join(sorted(actual)[:8]) or "none"
        return False, f"model is not an electrical model (classes: {sample}…)"
    return True, "electrical model"


class YoloDetector(BaseDetector):
    """Ultralytics YOLO object detector for electrical components."""

    name = "ultralytics-yolo"

    def __init__(
        self,
        model_path: str,
        confidence: float = 0.35,
        nms: float = 0.45,
        device: str = "auto",
        imgsz: int = 640,
    ) -> None:
        self._model_path = model_path
        self._confidence = confidence
        self._nms = nms
        self._device = device
        self._imgsz = imgsz
        self._model = None
        self._load_error: str | None = None
        self._resolved_device: str | None = None
        try:
            from ultralytics import YOLO

            model = YOLO(model_path)
            # Only accept models actually trained for electrical components.
            # A generic COCO model (like the bundled yolo11n copy) must never
            # masquerade as an electrical detector — its detections would be
            # mislabeled persons/bottles, i.e. fake AI results. The check can
            # be bypassed only with ALLOW_GENERIC_MODEL=true (experiments).
            reason = "identity accepted"
            if not get_settings().allow_generic_model:
                ok, reason = is_electrical_model(model.names)
                if not ok:
                    self._model = None
                    self._load_error = reason
                    logger.warning("Rejected %s: %s — using CV fallback detector.", model_path, reason)
                    return
            self._model = model
            logger.info("YOLO electrical model loaded from %s (%s)", model_path, reason)
        except Exception as exc:  # pragma: no cover - depends on environment
            self._load_error = str(exc)
            logger.warning("YOLO model unavailable (%s). Using fallback detector.", exc)

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def engine(self) -> str:
        return "ultralytics-yolo"

    def _resolve_device(self) -> str:
        """Resolve 'auto' to a concrete device ultralytics accepts.

        Recent ultralytics versions raise ``ValueError: Invalid CUDA 'device=auto'``
        when no CUDA device is present, so never hand 'auto' to predict().
        The resolved device is cached — it never changes at runtime.
        """
        if self._device != "auto":
            return self._device
        if self._resolved_device is None:
            import torch

            if torch.cuda.is_available():
                self._resolved_device = "cuda:0"
            elif torch.backends.mps.is_available():
                self._resolved_device = "mps"
            else:
                self._resolved_device = "cpu"
        return self._resolved_device

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self._model is None:
            return []
        results = self._model.predict(
            source=frame,
            conf=self._confidence,
            iou=self._nms,
            device=self._resolve_device(),
            imgsz=self._imgsz,
            verbose=False,
        )
        detections: list[Detection] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                label = CLASS_NAMES.get(cls_id, "unknown")
                detections.append(Detection(label=label, confidence=conf, bbox=(x1, y1, x2, y2), class_id=cls_id))
        return detections
