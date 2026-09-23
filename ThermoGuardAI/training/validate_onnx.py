"""Validate an ONNX model runs correctly with ONNX Runtime."""
from __future__ import annotations

import argparse

import cv2
import numpy as np


def validate(model_path: str, image_path: str | None = None) -> None:
    try:
        import onnxruntime as ort  # noqa: F401
    except ImportError as exc:
        raise SystemExit("onnxruntime not installed.") from exc

    import onnxruntime as ort

    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name
    shape = sess.get_inputs()[0].shape
    logger_n, logger_c, logger_h, logger_w = shape
    h, w = (int(logger_h), int(logger_w)) if isinstance(logger_h, int) else (640, 640)

    if image_path:
        img = cv2.imread(image_path)
        if img is None:
            raise SystemExit(f"Cannot read image {image_path}")
        img = cv2.resize(img, (w, h))
    else:
        img = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)

    blob = img.astype(np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))[None, ...]
    outputs = sess.run(None, {input_name: blob})
    print(f"ONNX OK: input={shape}, outputs={[o.shape for o in outputs]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate ONNX model")
    parser.add_argument("--model", default="models/electrical_yolo.onnx")
    parser.add_argument("--image", default=None)
    args = parser.parse_args()
    validate(args.model, args.image)
