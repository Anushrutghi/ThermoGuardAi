"""Camera management routes — including a real-time MJPEG live stream.

The stream endpoint reads the latest frame from the active CaptureManager
(a background thread keeps the camera grabbing continuously) and serves it
as multipart/x-mixed-replace so dashboards can embed a true live feed via a
plain <img> tag. When an inspection_id is supplied, each frame is also run
through the AI pipeline (detection → thermal → faults) and persisted, so the
stream doubles as the real-time inspection channel for local cameras.
"""
from __future__ import annotations

import asyncio
import logging

import jwt as pyjwt
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from ai.pipeline import encode_frame_jpeg
from backend.api.deps import get_current_user
from backend.core.config import get_settings
from backend.core.exceptions import AppError, CameraUnavailableError, UnauthorizedError
from backend.core.security import decode_access_token
from backend.db.session import open_session
from backend.models.user import User
from backend.schemas.camera import CameraCreate, CameraList
from backend.services import camera_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cameras", tags=["cameras"])

# Real-time stream guard-rails
_STREAM_BOUNDARY = b"--frame\r\n"
_STREAM_HEADER = b"Content-Type: image/jpeg\r\n\r\n"


def _require_user(token: str | None) -> int:
    """Authenticate stream/frame requests via ?token= (img tags can't send headers)."""
    if not token:
        raise UnauthorizedError("Missing token")
    try:
        payload = decode_access_token(token)
        return int(payload["sub"])
    except (pyjwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid or expired token") from exc


@router.get("", response_model=CameraList)
def list_cameras(_: User = Depends(get_current_user)) -> CameraList:  # noqa: B008
    data = camera_service.list_cameras()
    return CameraList(**data)


@router.post("/switch")
def switch_camera(payload: CameraCreate, _: User = Depends(get_current_user)) -> dict:  # noqa: B008
    try:
        return camera_service.switch_camera(payload.kind, payload.source)
    except Exception as exc:
        raise CameraUnavailableError(f"Could not start camera: {exc}") from exc


@router.post("/switch/phone")
def switch_phone(_: User = Depends(get_current_user)) -> dict:  # noqa: B008
    """Activate the mobile browser camera bridge (frames arrive via WS)."""
    try:
        return camera_service.switch_camera("phone")
    except Exception as exc:
        raise AppError(f"Could not activate phone bridge: {exc}") from exc


@router.post("/stop")
def stop_camera(_: User = Depends(get_current_user)) -> dict:  # noqa: B008
    """Release the active camera source — turns the webcam off."""
    return camera_service.stop_camera()


def _active_or_start() -> None:
    """Ensure a camera is capturing; fall back to the default webcam."""
    manager = camera_service.get_capture_manager()
    if manager.is_active():
        return
    try:
        manager.switch_to("webcam")
    except Exception:
        raise CameraUnavailableError(
            "No camera active and default webcam unavailable on this machine"
        ) from None
    if not manager.is_active():
        raise CameraUnavailableError("No camera active and default webcam unavailable")


def _wait_for_frame(manager, timeout_s: float = 3.0):  # noqa: ANN001
    """Return the first frame, waiting briefly for the camera to start."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = manager.latest_frame()
        if frame is not None:
            return frame
        time.sleep(0.05)
    return None


@router.get("/frame")
def camera_frame(
    token: str | None = Query(default=None),
    annotate: bool = False,
    thermal: bool = False,
    max_width: int | None = Query(default=None),
    quality: int | None = Query(default=None),
) -> Response:
    """Return the latest camera frame as a single JPEG (AI-annotated on request)."""
    _require_user(token)
    _active_or_start()
    manager = camera_service.get_capture_manager()
    frame = _wait_for_frame(manager)
    if frame is None:
        raise CameraUnavailableError("Camera has not produced a frame yet")

    out = frame
    if annotate:
        from backend.services.inspection_service import get_pipeline

        result = get_pipeline().process(frame, with_overlay=thermal)
        out = result.thermal_overlay if (thermal and result.thermal_overlay is not None) else result.frame

    settings = get_settings()
    jpeg = encode_frame_jpeg(
        out,
        quality=quality or settings.stream_jpeg_quality,
        max_width=max_width or settings.stream_max_width,
    )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/stream")
async def camera_stream(
    token: str | None = Query(default=None),
    inspection_id: int | None = Query(default=None),
    annotate: bool = True,
    thermal: bool = False,
    max_fps: float = Query(default=15.0, ge=1.0, le=60.0),
    max_frames: int | None = Query(default=None, ge=1, description="Stop after this many frames (tests / debugging)"),
    max_width: int | None = Query(default=None),
    quality: int | None = Query(default=None),
) -> StreamingResponse:
    """Live MJPEG stream of the active camera.

    - `annotate=true` (default) runs each frame through the AI pipeline so the
      feed shows live detection boxes, temperatures and risk overlay.
    - `inspection_id` (optional) additionally persists each frame into that
      inspection (frames, temperature readings, faults, alarms) — turning the
      stream into the real-time inspection channel for local cameras.
    - `thermal=true` blends the thermal colormap onto the annotated frame.
    - `max_frames` (optional) caps the number of streamed frames, after which
      the stream ends cleanly (useful for tests and short clips).
    """
    _require_user(token)
    _active_or_start()
    manager = camera_service.get_capture_manager()
    settings = get_settings()

    async def generate():
        loop = asyncio.get_event_loop()
        interval = 1.0 / max_fps
        service = None
        db = None
        close_db = None
        try:
            if inspection_id is not None:
                from backend.services.inspection_service import InspectionService

                db, close_db = open_session()
                service = InspectionService(db)
            frames_sent = 0
            while True:
                frame = manager.latest_frame()
                if frame is not None:
                    try:
                        if inspection_id is not None and service is not None:
                            result = await loop.run_in_executor(None, service.process_frame_raw, inspection_id, frame)
                            out = result.frame
                            if thermal and result.thermal_overlay is not None:
                                out = result.thermal_overlay
                        elif annotate:
                            from backend.services.inspection_service import get_pipeline

                            result = await loop.run_in_executor(
                                None, get_pipeline().process, frame, thermal
                            )
                            out = result.thermal_overlay if (thermal and result.thermal_overlay is not None) else result.frame
                        else:
                            out = frame
                        jpeg = await loop.run_in_executor(
                            None,
                            encode_frame_jpeg,
                            out,
                            quality or settings.stream_jpeg_quality,
                            max_width or settings.stream_max_width,
                        )
                        yield _STREAM_BOUNDARY + _STREAM_HEADER + jpeg + b"\r\n"
                        frames_sent += 1
                        if max_frames is not None and frames_sent >= max_frames:
                            break
                    except LookupError:
                        logger.warning("Inspection %s no longer exists; stopping stream", inspection_id)
                        break
                    except Exception:  # noqa: BLE001
                        logger.exception("Stream frame error")
                await asyncio.sleep(interval)
        finally:
            if close_db is not None:
                close_db()

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, no-transform"},
    )
