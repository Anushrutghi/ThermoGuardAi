"""Adaptive performance engine — self-tunes the pipeline to hit a target FPS.

Strategy (works on both low-end and high-end hardware):
  1. **Resolution scaling** — run detection on a downscaled copy of the
     frame (bboxes are remapped back to full resolution). This is the
     single biggest lever for the CV detector and small YOLO models.
  2. **Analysis stride** — when resolution hits the floor, the full
     analysis (detection + visual heuristics + thermal stats) runs at most
     every Nth frame; in between, cached results are re-used while the
     current frame is still annotated and streamed. This keeps the live
     video smooth (high FPS) even when analysis is slow.
  3. **Perf grade** — a coarse 'ultra | high | mid | low' grade lets the
     transport layer adapt stream width / JPEG quality accordingly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.core.config import get_settings

logger = logging.getLogger(__name__)

SCALE_STEP = 0.8  # multiplicative resolution step when tuning


@dataclass
class PerfTelemetry:
    """Snapshot of the controller's current tuning state."""

    target_fps: float
    achieved_fps: float
    scale: float
    stride: int
    grade: str
    ema_inference_ms: float
    frames_analyzed: int
    frames_total: int

    def to_json(self) -> dict:
        return {
            "target_fps": round(self.target_fps, 1),
            "achieved_fps": round(self.achieved_fps, 1),
            "scale": round(self.scale, 2),
            "stride": self.stride,
            "grade": self.grade,
            "ema_inference_ms": round(self.ema_inference_ms, 1),
            "frames_analyzed": self.frames_analyzed,
            "frames_total": self.frames_total,
        }


class AdaptivePerfController:
    """Adjusts detection scale and analysis stride to sustain a target FPS."""

    def __init__(
        self,
        target_fps: float | None = None,
        min_fps: float | None = None,
        min_scale: float | None = None,
        max_scale: float | None = None,
        max_stride: int | None = None,
        tune_interval_s: float | None = None,
    ) -> None:
        settings = get_settings()
        self.target_fps = target_fps or settings.target_fps
        self.min_fps = min_fps or settings.min_fps
        self.min_scale = min_scale or settings.perf_min_scale
        self.max_scale = max_scale or settings.perf_max_scale
        self.max_stride = max(1, int(max_stride or settings.perf_max_stride))
        self.tune_interval_s = tune_interval_s or settings.perf_tune_interval_s

        self.scale: float = self.max_scale
        self.stride: int = 1
        self.frames_total = 0
        self.frames_analyzed = 0
        self._ema_ms: float | None = None
        self._last_tune = time.monotonic()
        self._fps_window_start = time.monotonic()
        self._fps_window_frames = 0
        self._achieved_fps = 0.0

    # ------------------------------------------------------------------
    def should_analyze(self) -> bool:
        """Whether the current frame should run the full analysis."""
        return (self.frames_total % self.stride) == 0

    def update(self, inference_ms: float) -> PerfTelemetry:
        """Record one processed frame's wall time; periodically retune."""
        now = time.monotonic()
        self.frames_total += 1

        # EMA of per-frame processing time
        if self._ema_ms is None:
            self._ema_ms = float(inference_ms)
        else:
            self._ema_ms = 0.9 * self._ema_ms + 0.1 * float(inference_ms)

        # sliding-window FPS
        self._fps_window_frames += 1
        elapsed = now - self._fps_window_start
        if elapsed >= 1.0:
            self._achieved_fps = self._fps_window_frames / elapsed
            self._fps_window_frames = 0
            self._fps_window_start = now

        if now - self._last_tune >= self.tune_interval_s:
            self._tune(now)

        return self.telemetry()

    def telemetry(self) -> PerfTelemetry:
        return PerfTelemetry(
            target_fps=self.target_fps,
            achieved_fps=self._achieved_fps,
            scale=round(self.scale, 3),
            stride=self.stride,
            grade=self._grade(),
            ema_inference_ms=self._ema_ms or 0.0,
            frames_analyzed=self.frames_analyzed,
            frames_total=self.frames_total,
        )

    # ------------------------------------------------------------------
    def _tune(self, now: float) -> None:
        """Raise/lower load to approach the target FPS (no hysteresis thrash)."""
        if self._ema_ms is None or self._ema_ms <= 0:
            self._last_tune = now
            return

        achievable = 1000.0 / self._ema_ms
        target = self.target_fps

        if achievable < self.min_fps * 1.1:
            # Severely under target — cut load aggressively
            self._reduce_load(aggressive=True)
        elif achievable < target * 0.85:
            # A bit slow — mild reduction
            self._reduce_load(aggressive=False)
        elif achievable > target * 1.25 and self.scale < self.max_scale:
            # Headroom available — raise resolution back up
            self.scale = min(self.max_scale, self.scale / SCALE_STEP)
            logger.debug("Perf: raising scale to %.2f (achievable %.0f fps)", self.scale, achievable)
        elif achievable > target * 1.35 and self.stride > 1:
            # Plenty of headroom — analyze more often again
            self.stride = max(1, self.stride - 1)
            logger.debug("Perf: lowering stride to %d (achievable %.0f fps)", self.stride, achievable)

        self._last_tune = now

    def _reduce_load(self, aggressive: bool) -> None:
        step = SCALE_STEP if not aggressive else SCALE_STEP * SCALE_STEP
        if self.scale > self.min_scale:
            self.scale = max(self.min_scale, self.scale * step)
            logger.debug("Perf: lowering scale to %.2f", self.scale)
        elif self.stride < self.max_stride:
            self.stride += 1
            logger.debug("Perf: raising stride to %d", self.stride)
        else:
            logger.debug("Perf: at minimum load already (scale=%.2f stride=%d)", self.scale, self.stride)

    def _grade(self) -> str:
        """Coarse performance grade used by the transport layer."""
        if self._achieved_fps >= self.target_fps * 0.9 and self.scale >= self.max_scale and self.stride == 1:
            return "ultra"
        if self._achieved_fps >= self.target_fps * 0.7:
            return "high"
        if self._achieved_fps >= self.target_fps * 0.45:
            return "mid"
        return "low"
