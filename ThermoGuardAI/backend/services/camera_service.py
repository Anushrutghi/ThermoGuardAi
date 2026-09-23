"""Camera service — singleton capture manager facade."""
from __future__ import annotations

import logging

from camera.capture_manager import CaptureManager

logger = logging.getLogger(__name__)

_manager: CaptureManager | None = None


def get_capture_manager() -> CaptureManager:
    global _manager
    if _manager is None:
        _manager = CaptureManager()  # target FPS from settings (default 60)
    return _manager


def switch_camera(kind: str, source: str | None = None, **kwargs) -> dict:
    """Switch the active camera and return its status."""
    manager = get_capture_manager()
    manager.switch_to(kind, source, **kwargs)
    return {"active_id": manager.active_id, "kind": kind, "status": "running"}


def stop_camera() -> dict:
    """Release the active camera (turns the webcam off)."""
    manager = get_capture_manager()
    if manager.is_active():
        manager.stop()
        return {"active_id": None, "status": "stopped"}
    return {"active_id": None, "status": "already_stopped"}


def list_cameras() -> dict:
    manager = get_capture_manager()
    return {
        "items": manager.available_cameras(),
        "active_id": manager.active_id,
        "active_kind": _active_kind(manager),
        "fps": round(manager.measured_fps, 1),
    }


def _active_kind(manager: CaptureManager) -> str | None:
    if not manager.is_active():
        return None
    try:
        return manager._capturer._source.info.kind  # noqa: SLF001
    except Exception:
        return None
