"""Benchmark the adaptive inspection pipeline.

Measures achieved FPS, adaptive scale/stride and grade on synthetic frames,
demonstrating that the pipeline self-tunes to the machine. Usage:

    .venv/bin/python scripts/benchmark_fps.py --frames 300 --target-fps 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# temporarily raise the target from env (pipeline reads it from settings on construction)
import os  # noqa: E402

from ai.pipeline import InspectionPipeline  # noqa: E402
from backend.core.config import get_settings  # noqa: E402


def _synthetic_frame(index: int) -> np.ndarray:
    """A panel-like frame with modules and a hotspot (deterministic)."""
    rng = np.random.default_rng(index)
    frame = rng.integers(30, 60, (480, 640, 3), dtype=np.uint8)
    frame[80:240, 60:180] = (28, 28, 33)
    frame[80:240, 220:340] = (26, 26, 31)
    frame[80:240, 380:500] = (30, 30, 35)
    frame[300:380, 420:520] = (238, 238, 238)
    cv2.circle(frame, (440, 340), 24, (245, 245, 245), -1)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark adaptive pipeline FPS")
    parser.add_argument("--frames", type=int, default=240)
    parser.add_argument("--target-fps", type=float, default=None)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--detector", choices=["auto", "yolo", "fallback"], default="fallback", help="detector to benchmark")
    parser.add_argument("--model", default=None, help="path to YOLO weights (default: settings MODEL_PATH)")
    parser.add_argument("--device", default=None, help="inference device: auto|cpu|cuda|mps")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO inference size (default 640)")
    args = parser.parse_args()

    # allow overriding env-driven settings via CLI without touching the .env
    changed = False
    if args.target_fps:
        os.environ["TARGET_FPS"] = str(args.target_fps)
        changed = True
    if args.model:
        os.environ["MODEL_PATH"] = args.model
        changed = True
    if args.device:
        os.environ["INFERENCE_DEVICE"] = args.device
        changed = True
    if changed:
        get_settings.cache_clear()

    pipeline = InspectionPipeline(detector_mode=args.detector)
    if args.detector == "yolo" and hasattr(pipeline.detector, "_imgsz"):
        pipeline.detector._imgsz = args.imgsz  # noqa: SLF001
    print(f"Detector: {pipeline.detector.engine} · Thermal: {pipeline.thermal.name}")
    print(f"Target FPS: {pipeline.perf.target_fps} · tuning every {pipeline.perf.tune_interval_s}s")
    print(f"{'frame':>6} {'analyzed':>8} {'scale':>6} {'stride':>6} {'ema_ms':>8} {'fps':>7} {'grade':>6}")

    start = time.monotonic()
    last_log = 0.0
    frames = 0
    for i in range(args.frames):
        frame = _synthetic_frame(i)
        if (args.width, args.height) != (640, 480):
            frame = cv2.resize(frame, (args.width, args.height))
        result = pipeline.process(frame)
        frames += 1
        now = time.monotonic()
        if now - last_log >= 1.0 and i > 5:
            t = pipeline.perf.telemetry()
            print(
                f"{i:>6} {str(result.analyzed):>8} {t.scale:>6.2f} {t.stride:>6} {t.ema_inference_ms:>8.1f} "
                f"{t.achieved_fps:>7.1f} {t.grade:>6}"
            )
            last_log = now

    elapsed = time.monotonic() - start
    overall = frames / elapsed
    t = pipeline.perf.telemetry()
    print("\n===== RESULT =====")
    print(f"Overall throughput: {overall:.1f} FPS across {frames} frames ({elapsed:.1f}s)")
    print(f"Final tuning: scale={t.scale} stride={t.stride} grade={t.grade}")
    print(f"Analyzed {t.frames_analyzed}/{t.frames_total} frames ({t.frames_analyzed / max(1, t.frames_total) * 100:.0f}%)")
    print("Note: 'achieved_fps' is measured by the controller's 1s window; overall is wall-clock.")
    if overall < 15:
        print("⚠️  Below 15 FPS on this machine — the controller is already at minimum load;")
        print("   consider INFERENCE_DEVICE=cuda/mps or installing the AI stack (requirements-ai.txt).")


if __name__ == "__main__":
    main()
