"""S4 WebSocket security tests.

A malicious client must not be able to: start an inspection for another
organization's device, inject arbitrary device IDs, bypass the camera
workflow, run duplicate inspections on one connection, or have a gracefully
stopped inspection re-marked aborted by a disconnect.
"""
from __future__ import annotations

import base64
import uuid

import cv2

from backend.core.security import hash_password
from backend.db.session import SessionLocal
from backend.models.device import Device
from backend.models.user import User
from tests.switch_test_utils import make_switch_frame

PASSWORD = "password123"


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _login(client, username: str) -> tuple[dict[str, str], str]:  # noqa: ANN001
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, token


def _add_org_user(organization: str) -> str:
    db = SessionLocal()
    try:
        user = User(
            username=_unique(f"ws_{organization.lower()}"),
            email=f"{_unique(organization.lower())}@test.local",
            full_name=organization,
            hashed_password=hash_password(PASSWORD),
            role="admin",
            organization=organization,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.username
    finally:
        db.close()


def _add_org_device(organization: str) -> int:
    db = SessionLocal()
    try:
        device = Device(
            name=f"{organization} Switch",
            device_type="wall_switch",
            organization=organization,
            status="ACTIVE",
        )
        db.add(device)
        db.commit()
        return device.id
    finally:
        db.close()


def test_ws_duplicate_start_rejected(client) -> None:  # noqa: ANN001
    username = _add_org_user("OrgWsDup")
    _, token = _login(client, username)
    device_id = _add_org_device("OrgWsDup")

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "quick", "device_id": device_id})
        assert ws.receive_json()["type"] == "ready"
        # second start while one is running → explicit error, no silent duplicate
        ws.send_json({"type": "start_inspection", "mode": "quick", "device_id": device_id})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "already running" in msg["message"]


def test_ws_malformed_and_unknown_messages(client) -> None:  # noqa: ANN001
    username = _add_org_user("OrgWsMsg")
    _, token = _login(client, username)

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_text("{not-json")
        msg = ws.receive_json()
        assert msg["type"] == "error" and "Invalid JSON" in msg["message"]

        ws.send_json({"type": "no_such_message", "payload": "x"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and "Unknown message type" in msg["message"]

        # frame before any inspection → rejected
        ok, buf = cv2.imencode(".jpg", make_switch_frame())
        b64 = base64.b64encode(buf.tobytes()).decode()
        ws.send_json({"type": "frame", "jpeg_base64": b64})
        msg = ws.receive_json()
        assert msg["type"] == "error" and "Start an inspection first" in msg["message"]


def test_ws_cross_org_device_start_rejected(client) -> None:  # noqa: ANN001
    user_a = _add_org_user("OrgWsA")
    device_b = _add_org_device("OrgWsB")
    _, token_a = _login(client, user_a)

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token_a}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "quick", "device_id": device_b})
        msg = ws.receive_json()
        assert msg["type"] == "error", msg
        assert "not found or not authorized" in msg["message"]

    # and the foreign inspection was never created
    db = SessionLocal()
    try:
        from backend.models.inspection import Inspection

        assert db.query(Inspection).filter(Inspection.device_id == device_b).count() == 0
    finally:
        db.close()


def test_ws_stop_then_disconnect_keeps_completed(client) -> None:  # noqa: ANN001
    """A graceful stop must survive the disconnect (no abort-after-complete)."""
    username = _add_org_user("OrgWsStop")
    _, token = _login(client, username)
    device_id = _add_org_device("OrgWsStop")

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "quick", "device_id": device_id})
        inspection_id = ws.receive_json()["inspection_id"]
        ws.send_json({"type": "stop_inspection"})
        msg = ws.receive_json()
        assert msg["type"] == "stopped"
        # connection closes right after — the finally block must NOT abort it

    db = SessionLocal()
    try:
        from backend.models.inspection import Inspection

        inspection = db.get(Inspection, inspection_id)
        assert inspection is not None
        assert inspection.status == "completed", "gracefully stopped inspection must stay completed"
    finally:
        db.close()


def test_ws_disconnect_aborts_only_running(client) -> None:  # noqa: ANN001
    username = _add_org_user("OrgWsAbort")
    _, token = _login(client, username)
    device_id = _add_org_device("OrgWsAbort")

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "quick", "device_id": device_id})
        inspection_id = ws.receive_json()["inspection_id"]
        # disconnect without stopping → the running inspection is aborted

    # the server aborts asynchronously on disconnect — poll briefly
    import time

    deadline = time.monotonic() + 5.0
    status = None
    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            from backend.models.inspection import Inspection

            inspection = db.get(Inspection, inspection_id)
            status = inspection.status if inspection else None
        finally:
            db.close()
        if status == "aborted":
            break
        time.sleep(0.1)
    assert status == "aborted", f"expected aborted, got {status}"


def test_ws_duplicate_stop_is_harmless(client) -> None:  # noqa: ANN001
    username = _add_org_user("OrgWsDupStop")
    _, token = _login(client, username)
    device_id = _add_org_device("OrgWsDupStop")

    with client.websocket_connect(f"/api/v1/ws/inspect?token={token}") as ws:
        ws.send_json({"type": "start_inspection", "mode": "quick", "device_id": device_id})
        inspection_id = ws.receive_json()["inspection_id"]
        for _ in range(2):
            ws.send_json({"type": "stop_inspection"})
            msg = ws.receive_json()
            assert msg["type"] == "stopped", msg

    db = SessionLocal()
    try:
        from backend.models.inspection import Inspection

        inspection = db.get(Inspection, inspection_id)
        assert inspection.status == "completed"
        assert inspection.ended_at is not None
    finally:
        db.close()
