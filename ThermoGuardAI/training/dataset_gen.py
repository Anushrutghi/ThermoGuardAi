"""Synthetic dataset generator for training a YOLO electrical-panel model.

Renders photorealistic-ish synthetic electrical panel images with
component bounding boxes and YOLO-format labels, so the training
pipeline can be exercised before real labeled data is available.

Usage:
    python -m training.dataset_gen --samples 300 --out datasets/synthetic
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np
import yaml

from ai.detector.base import COMPONENT_CLASSES

CLASS_TO_INDEX = {name: i for i, name in enumerate(COMPONENT_CLASSES)}

# (class, color, aspect-ratio range, size range as fraction of frame)
_TEMPLATES = {
    "circuit_breaker": ((40, 40, 45), (1.6, 2.2), (0.06, 0.14)),
    "fuse": ((200, 180, 60), (3.5, 5.0), (0.03, 0.08)),
    "relay": ((35, 60, 90), (1.2, 1.6), (0.05, 0.1)),
    "contactor": ((45, 55, 70), (1.4, 1.8), (0.06, 0.12)),
    "busbar": ((150, 120, 60), (8.0, 14.0), (0.1, 0.3)),
    "terminal": ((180, 180, 170), (2.0, 3.0), (0.02, 0.05)),
    "cable": ((30, 30, 30), (6.0, 12.0), (0.05, 0.2)),
    "mccb": ((45, 45, 50), (2.0, 2.6), (0.06, 0.12)),
    "mcb": ((55, 55, 60), (1.8, 2.4), (0.04, 0.08)),
    "rccb": ((50, 50, 55), (2.0, 2.6), (0.05, 0.1)),
    "transformer": ((70, 60, 55), (1.2, 1.6), (0.1, 0.2)),
    "motor_starter": ((60, 65, 70), (1.2, 1.6), (0.08, 0.15)),
    "disconnect_switch": ((75, 75, 80), (1.4, 1.9), (0.06, 0.12)),
    "power_supply": ((90, 90, 95), (2.4, 3.2), (0.07, 0.13)),
    "indicator_light": ((120, 60, 60), (1.0, 1.2), (0.01, 0.03)),
    "panel_door": ((120, 120, 125), (1.2, 1.6), (0.2, 0.4)),
    "warning_label": ((220, 200, 40), (1.6, 2.4), (0.05, 0.12)),
}


def _render_panel(rng: random.Random, width: int = 960, height: int = 640) -> tuple[np.ndarray, list[dict]]:
    """Render a synthetic panel image + YOLO annotations (xyxy)."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # background: dark panel enclosure with subtle gradient
    img[:, :] = rng.choice([(30, 32, 36), (36, 38, 42), (28, 30, 34)])
    _add_grain(img, rng)

    annotations: list[dict] = []
    attempts = 0
    placed = 0
    max_items = rng.randint(4, 9)
    while placed < max_items and attempts < 60:
        attempts += 1
        cls = rng.choice(COMPONENT_CLASSES)
        base_color, ar_range, size_range = _TEMPLATES[cls]
        w_frac = rng.uniform(*size_range)
        h_frac = w_frac / rng.uniform(*ar_range)
        if cls == "busbar":
            w_frac, h_frac = rng.uniform(0.25, 0.6), rng.uniform(0.02, 0.05)
        if cls == "cable":
            w_frac, h_frac = rng.uniform(0.3, 0.8), rng.uniform(0.01, 0.03)
            if rng.random() < 0.5:
                w_frac, h_frac = h_frac, w_frac

        x = rng.uniform(0.03, 0.97 - w_frac)
        y = rng.uniform(0.03, 0.97 - h_frac)
        x1, y1 = int(x * width), int(y * height)
        x2, y2 = int((x + w_frac) * width), int((y + h_frac) * height)
        if x2 <= x1 or y2 <= y1:
            continue

        color = tuple(int(c * rng.uniform(0.75, 1.15)) for c in base_color)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, -1)
        _add_face_details(img, (x1, y1, x2, y2), cls, rng)
        annotations.append({"class": cls, "bbox": [x1, y1, x2, y2]})
        placed += 1

    # background panel door outline
    cv2.rectangle(img, (4, 4), (width - 5, height - 5), (80, 80, 85), 3)
    return img, annotations


def _add_face_details(img: np.ndarray, bbox: list[int], cls: str, rng: random.Random) -> None:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    # handle / toggle / screw hints
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    if cls in ("circuit_breaker", "mcb", "mccb", "rccb", "disconnect_switch", "relay", "contactor"):
        cv2.rectangle(img, (x1 + w // 4, y1 + h // 4), (x2 - w // 4, y2 - h // 4), (15, 15, 18), -1)
    if cls == "indicator_light":
        cv2.circle(img, (cx, cy), max(3, min(w, h) // 2 - 1), rng.choice([(0, 0, 255), (0, 200, 0), (0, 200, 255)]), -1)
    for sx, sy in ((x1 + 6, y1 + 6), (x2 - 6, y1 + 6), (x1 + 6, y2 - 6), (x2 - 6, y2 - 6)):
        cv2.circle(img, (sx, sy), 2, (10, 10, 12), -1)


def _add_grain(img: np.ndarray, rng: random.Random) -> None:
    noise = np.random.default_rng(rng.randrange(10**9)).normal(0, 2.5, img.shape)
    img[:] = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def to_yolo_bbox(bbox: list[int], width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
    bw, bh = (x2 - x1) / width, (y2 - y1) / height
    return [round(cx, 5), round(cy, 5), round(bw, 5), round(bh, 5)]


def generate_dataset(samples: int, out_dir: str, split: tuple[float, float, float] = (0.7, 0.15, 0.15)) -> None:
    out = Path(out_dir)
    rng = random.Random(42)
    for sub, _frac in zip(("train", "val", "test"), split, strict=False):
        (out / "images" / sub).mkdir(parents=True, exist_ok=True)
        (out / "labels" / sub).mkdir(parents=True, exist_ok=True)

    count = {sub: int(samples * f) for sub, f in zip(("train", "val", "test"), split, strict=False)}
    for i in range(samples):
        sub = min(count, key=lambda k: (count[k] <= 0, -count[k])) if count else "train"
        count[sub] -= 1
        img, annotations = _render_panel(rng)
        stem = f"panel_{i:05d}"
        cv2.imwrite(str(out / "images" / sub / f"{stem}.jpg"), img)
        h, w = img.shape[:2]
        lines = []
        for ann in annotations:
            cx, cy, bw, bh = to_yolo_bbox(ann["bbox"], w, h)
            lines.append(f"{CLASS_TO_INDEX[ann['class']]} {cx} {cy} {bw} {bh}")
        (out / "labels" / sub / f"{stem}.txt").write_text("\n".join(lines) + "\n")

    # dataset.yaml for ultralytics
    dataset_yaml = {
        "path": str(out.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {i: n for i, n in enumerate(COMPONENT_CLASSES)},
    }
    (out / "dataset.yaml").write_text(yaml.safe_dump(dataset_yaml, sort_keys=False))
    print(f"Dataset generated: {out} ({samples} samples)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic YOLO dataset")
    parser.add_argument("--samples", type=int, default=300)
    parser.add_argument("--out", default="datasets/synthetic")
    args = parser.parse_args()
    generate_dataset(args.samples, args.out)
