"""Thread-safe frame buffer with a background capture worker."""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np


class FrameBuffer:
    """Bounded buffer holding the most recent frames with timestamps."""

    def __init__(self, maxlen: int = 30) -> None:
        self._frames: deque[tuple[float, np.ndarray]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frames.append((time.time(), frame))

    def latest(self) -> tuple[float, np.ndarray] | None:
        with self._lock:
            if not self._frames:
                return None
            return self._frames[-1]

    def snapshot(self) -> list[tuple[float, np.ndarray]]:
        with self._lock:
            return list(self._frames)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)


class BackgroundCapturer:
    """Runs a camera source in a background thread at a target FPS.

    Decouples camera I/O from inference so slow frames never block the
    dashboard, and slow inference never blocks frame capture.
    """

    def __init__(self, source, target_fps: float = 15.0, buffer_size: int = 15) -> None:
        self._source = source
        self._interval = 1.0 / max(1.0, target_fps)
        self.buffer = FrameBuffer(buffer_size)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._measured_fps = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._source.start()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="camera-capturer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        frames = 0
        t0 = time.time()
        while not self._stop_event.is_set():
            frame = self._source.read()
            if frame is not None:
                self.buffer.push(frame)
                frames += 1
            elapsed = time.time() - t0
            if elapsed >= 0.5:
                self._measured_fps = frames / elapsed
                frames = 0
                t0 = time.time()
            time.sleep(max(0.001, self._interval - (elapsed % self._interval)))

    @property
    def measured_fps(self) -> float:
        return self._measured_fps

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._source.stop()
