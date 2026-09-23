"""Detector factory — picks the right detector based on configuration."""
from __future__ import annotations

import logging

from ai.detector.base import BaseDetector
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

_instances: dict[str, BaseDetector] = {}


def get_detector(mode: str | None = None) -> BaseDetector:
    """Return a detector singleton. mode: auto|yolo|fallback."""
    settings = get_settings()
    requested = mode or settings.detector_mode

    cache_key = requested
    if cache_key in _instances:
        return _instances[cache_key]

    if requested == "yolo":
        from ai.detector.yolo_detector import YoloDetector

        detector: BaseDetector = YoloDetector(
            model_path=str(settings.model_full_path),
            confidence=settings.confidence_threshold,
            nms=settings.nms_threshold,
            device=settings.inference_device,
        )
    elif requested == "fallback":
        from ai.detector.fallback_detector import FallbackDetector

        detector = FallbackDetector(min_confidence=settings.fallback_min_confidence)
    else:  # auto
        from ai.detector.yolo_detector import YoloDetector

        yolo = YoloDetector(
            model_path=str(settings.model_full_path),
            confidence=settings.confidence_threshold,
            nms=settings.nms_threshold,
            device=settings.inference_device,
        )
        if yolo.available:
            detector = yolo
        else:
            from ai.detector.fallback_detector import FallbackDetector

            detector = FallbackDetector(min_confidence=settings.fallback_min_confidence)
            logger.info("Auto mode: using CV fallback detector (no YOLO weights found).")

    _instances[cache_key] = detector
    return detector
