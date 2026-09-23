"""Export a trained YOLO model to ONNX (for edge / Jetson deployment)."""
from __future__ import annotations

import argparse
import sys


def export(model_path: str, dynamic: bool = True) -> None:
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ultralytics is not installed. Run: make setup-ai")

    yolo = YOLO(model_path)
    out = yolo.export(format="onnx", dynamic=dynamic, simplify=True)
    print(f"Exported ONNX model: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export YOLO to ONNX")
    parser.add_argument("--model", default="models/electrical_yolo.pt")
    args = parser.parse_args()
    export(args.model)
