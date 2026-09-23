"""Unit tests for camera sources and the frame buffer."""
from __future__ import annotations

import time

import numpy as np

from camera.factory import create_camera
from camera.frame_buffer import BackgroundCapturer, FrameBuffer
from camera.sources.phone import PhoneCameraSource


def _fake_jpeg() -> bytes:
    import cv2

    frame = np.full((120, 160, 3), 200, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


def test_frame_buffer_ring() -> None:
    buf = FrameBuffer(maxlen=3)
    for i in range(5):
        buf.push(np.full((2, 2, 3), i, dtype=np.uint8))
    assert len(buf) == 3
    latest = buf.latest()
    assert latest is not None and int(latest[1][0, 0, 0]) == 4


def test_phone_source_push_and_read() -> None:
    src = PhoneCameraSource(session_id="test-phone")
    assert not src.available
    src.push_frame(_fake_jpeg())
    frame = src.read()
    assert frame is not None
    assert frame.shape == (120, 160, 3)
    assert src.available


def test_file_source_image_loop() -> None:
    from pathlib import Path

    import cv2

    path = Path("database/_tmp_test.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((64, 64, 3), 128, dtype=np.uint8))
    try:
        src = create_camera("file", str(path))
        src.start()
        f1 = src.read()
        f2 = src.read()
        assert f1 is not None and f2 is not None
        assert f1.shape == (64, 64, 3)
        assert np.array_equal(f1, f2)  # image source returns same frame
    finally:
        path.unlink(missing_ok=True)


def test_background_capturer_fps_measurement() -> None:
    class DummySource:
        def __init__(self) -> None:
            self.info = type("I", (), {"id": "dummy", "name": "Dummy", "kind": "webcam"})()
            self.started = False

        def start(self) -> None:
            self.started = True

        def read(self) -> np.ndarray | None:
            return np.zeros((10, 10, 3), dtype=np.uint8)

        def stop(self) -> None:
            pass

    cap = BackgroundCapturer(DummySource(), target_fps=20.0)
    cap.start()
    time.sleep(0.7)  # > 0.5s measurement window
    latest = cap.buffer.latest()
    assert latest is not None
    assert cap.measured_fps > 0
    cap.stop()
