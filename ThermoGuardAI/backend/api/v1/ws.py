"""WebSocket routes — live inspection feed and mobile camera bridge.

Protocol (JSON messages):
  client -> server:
    {"type": "start_inspection", "mode": "quick", "panel_id": null}
    {"type": "frame", "jpeg_base64": "..."}              # from phone/desktop browser
    {"type": "stop_inspection"}
    {"type": "ping"}
  server -> client:
    {"type": "ready", "inspection_id": N}
    {"type": "result", "jpeg_base64": "...", "data": {...}}
    {"type": "alarm", "severity": "...", "message": "..."}
    {"type": "error", "message": "..."}
    {"type": "pong"}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai.pipeline import encode_frame_jpeg
from ai.switch.controller import SwitchInspectionController, draw_switch_overlay
from backend.api.deps import decode_and_get_user
from backend.core.config import get_settings
from backend.core.exceptions import UnauthorizedError
from backend.db.session import open_session
from backend.services.inspection_service import InspectionService
from backend.services.switch_service import SwitchInspectionService
from camera.sources.phone import PhoneCameraSource

logger = logging.getLogger(__name__)
router = APIRouter()

# Guard-rail for the async loop: inference is CPU-bound and must not block the
# event loop, so each WS client gets its own executor.
_MAX_FRAME_BYTES = 8 * 1024 * 1024  # 8 MB cap on incoming JPEG payloads


def _auth_user(token: str | None) -> tuple[int, str | None]:
    """Authenticate the WebSocket via ?token= query param.

    Requires a valid JWT — unauthenticated connections are rejected.
    Returns (user_id, organization) so inspection creation is org-scoped.
    """
    if not token:
        raise UnauthorizedError("Missing token")
    db, close = open_session()
    try:
        user = decode_and_get_user(token, db)
        return user.id, user.organization
    finally:
        close()


@router.websocket("/ws/inspect")
async def ws_inspect(websocket: WebSocket) -> None:
    """Full-duplex live inspection channel.

    The browser sends frames (JPEG base64) captured via getUserMedia or
    from the local webcam; the server returns annotated frames + JSON.
    """
    # Authenticate BEFORE accepting so bad tokens get a clean 403-style close
    try:
        user_id, user_organization = _auth_user(websocket.query_params.get("token"))
    except UnauthorizedError:
        logger.warning("WS connect rejected: missing/invalid token")
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info("WS client connected (user=%s org=%s)", user_id, user_organization)
    inspection_id: int | None = None
    service: InspectionService | None = None
    controller: SwitchInspectionController | None = None
    switch_finalized = False
    db, close_db = open_session()
    phone = PhoneCameraSource(session_id=f"phone-{user_id}")
    executor = asyncio.get_event_loop().run_in_executor

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON message"}))
                continue

            mtype = msg.get("type", "")

            if mtype == "start_inspection":
                if inspection_id is not None and service is not None:
                    # A connection may only run ONE inspection at a time — a
                    # second start while one is running is rejected (no silent
                    # duplicate creation).
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "An inspection is already running on this connection"})
                    )
                    continue
                mode = msg.get("mode", "continuous")
                service = InspectionService(db)
                switch_finalized = False
                controller = SwitchInspectionController() if mode == "switch_first" else None
                try:
                    inspection = service.start_inspection(
                        user_id=user_id,
                        mode=mode,
                        panel_id=msg.get("panel_id"),
                        device_id=msg.get("device_id"),
                        camera_source="browser-webrtc",
                        notes=msg.get("notes"),
                        organization=user_organization,
                    )
                except Exception:  # noqa: BLE001 — do not leak internals
                    logger.exception("WS start_inspection failed (user=%s)", user_id)
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Could not start inspection (device/panel not found or not authorized)"})
                    )
                    continue
                inspection_id = inspection.id
                logger.info("WS inspection started (user=%s inspection=%s code=%s)", user_id, inspection_id, inspection.inspection_code)
                await websocket.send_text(json.dumps({"type": "ready", "inspection_id": inspection_id}))

            elif mtype == "frame":
                if inspection_id is None or service is None:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Start an inspection first"}))
                    continue
                jpeg_b64 = msg.get("jpeg_base64", "")
                if len(jpeg_b64) > _MAX_FRAME_BYTES * 2:  # base64 inflates ~33%
                    await websocket.send_text(json.dumps({"type": "error", "message": "Frame too large"}))
                    continue
                try:
                    jpeg = base64.b64decode(jpeg_b64)
                except (ValueError, TypeError):
                    continue
                if not jpeg:
                    continue
                phone.push_frame(jpeg)
                frame = phone.read()
                if frame is None:
                    continue

                # S1 switch-first mode: the controller drives the whole
                # workflow (search → stable → ROI → thermal scan → analysis).
                if controller is not None:
                    settings = get_settings()
                    try:
                        status = await executor(None, controller.on_frame, frame)
                    except Exception:  # noqa: BLE001
                        logger.exception("Switch controller error")
                        await websocket.send_text(json.dumps({"type": "error", "message": "Frame processing failed — retrying"}))
                        continue
                    bbox = tuple(status["switch_bbox"]) if status.get("switch_bbox") else None
                    roi = tuple(status["roi"]) if status.get("roi") else None
                    annotated = draw_switch_overlay(
                        frame, bbox, roi, status.get("state_label", ""),
                        status.get("switch_confidence") or None, status.get("message") or None,
                    )
                    annotated_jpeg = encode_frame_jpeg(
                        annotated, quality=settings.stream_jpeg_quality, max_width=settings.stream_max_width
                    )
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "switch_status",
                                "jpeg_base64": base64.b64encode(annotated_jpeg).decode(),
                                "data": status,
                            }
                        )
                    )
                    # Persist scan samples only while the scan is running — a
                    # completed scan must never keep writing rows.
                    if status.get("scan_sample") and not status.get("complete"):
                        try:
                            SwitchInspectionService(db).persist_scan_sample(
                                service.inspections.get_or_raise(inspection_id), status
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception("Failed to persist switch scan sample")
                    if status.get("complete") and not switch_finalized:
                        switch_finalized = True
                        inspection = service.inspections.get_or_raise(inspection_id)
                        # thermal read is CPU-light but driver I/O — keep it off
                        # the event loop like the frame processing
                        thermal_frame = None
                        try:
                            src = controller._thermal()  # noqa: SLF001
                            if getattr(src, "available", False):
                                thermal_frame = await executor(None, controller._read_thermal, frame)  # noqa: SLF001
                        except Exception:  # noqa: BLE001
                            logger.exception("Thermal read failed during finalize")
                        try:
                            SwitchInspectionService(db).finalize(
                                inspection, status, frame, annotated, thermal_frame=thermal_frame
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception("Failed to finalize switch inspection")
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "switch_complete",
                                    "inspection_id": inspection_id,
                                    "data": status.get("complete"),
                                    "device_id": inspection.device_id,
                                }
                            )
                        )
                    continue

                # CPU-bound inference + DB writes run in a thread to keep the
                # event loop responsive for other clients and the REST API.
                try:
                    result = await executor(None, service.process_frame_raw, inspection_id, frame)
                except Exception:  # noqa: BLE001
                    logger.exception("Frame processing error")
                    await websocket.send_text(json.dumps({"type": "error", "message": "Frame processing failed"}))
                    continue
                settings = get_settings()
                # Transport adapts to device grade: low-end devices get smaller,
                # lower-quality JPEGs (big bandwidth/CPU lever for the browser).
                grade = result.perf.grade if result.perf else "mid"
                quality = settings.stream_jpeg_quality
                max_width = settings.stream_max_width
                if grade == "low":
                    quality = settings.stream_min_jpeg_quality
                    max_width = int(settings.stream_max_width * 0.6)
                elif grade == "mid":
                    quality = int((settings.stream_jpeg_quality + settings.stream_min_jpeg_quality) / 2)
                annotated = encode_frame_jpeg(result.frame, quality=quality, max_width=max_width)
                payload = {
                    "type": "result",
                    "jpeg_base64": base64.b64encode(annotated).decode(),
                    "data": result.to_json(),
                }
                await websocket.send_text(json.dumps(payload))
                # Alarm notifications use the service's cooldown-filtered events,
                # so clients aren't spammed on every analyzed frame.
                for event in result.alarm_events:
                    await websocket.send_text(
                        json.dumps({"type": "alarm", **event})
                    )

            elif mtype == "stop_inspection":
                if inspection_id is not None and service is not None:
                    try:
                        service.stop_inspection(inspection_id, msg.get("notes"))
                        logger.info("WS inspection stopped (user=%s inspection=%s)", user_id, inspection_id)
                    except LookupError:
                        pass
                # Always ack a stop (idempotent) so clients never wait forever
                # on a duplicate stop or a stop with nothing running.
                await websocket.send_text(json.dumps({"type": "stopped", "inspection_id": inspection_id}))
                inspection_id = None
                controller = None

            elif mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            else:
                # Malformed/unknown messages are rejected explicitly instead of
                # being silently ignored (S4 message validation).
                await websocket.send_text(
                    json.dumps({"type": "error", "message": f"Unknown message type: {str(mtype)[:64]}"})
                )

    except WebSocketDisconnect:
        logger.info("WS client disconnected (user=%s inspection=%s)", user_id, inspection_id)
    except Exception:
        logger.exception("WS error (user=%s)", user_id)
    finally:
        if inspection_id is not None and service is not None:
            try:
                service.abort_inspection(inspection_id)  # no-op unless still running
            except Exception:
                pass
        phone.disconnect()
        close_db()
