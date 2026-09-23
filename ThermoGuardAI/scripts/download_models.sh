#!/usr/bin/env bash
# ============================================================
# Download pretrained YOLO weights for out-of-the-box detection.
# ThermoGuard runs with the CV fallback detector without these;
# installing them enables real neural inference.
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models

PY=.venv/bin/python
[[ -x "$PY" ]] || { echo "No .venv found. Run ./scripts/setup.sh --full first."; exit 1; }

MODEL="${1:-yolo11n.pt}"

echo "==> Downloading ${MODEL} (generic pretrained COCO model)"
if [[ -f "models/${MODEL}" ]]; then
  echo "Already present: models/${MODEL}"
else
  "$PY" - <<PYEOF
from ultralytics import YOLO
YOLO("${MODEL}")  # triggers download into working dir
import shutil, os
src = "${MODEL}"
if os.path.exists(src):
    shutil.move(src, "models/${MODEL}")
print("Downloaded -> models/${MODEL}")
PYEOF
fi

echo
echo "NOTE: This is a generic model. For electrical components, train your own:"
echo "  python -m training.dataset_gen --samples 400"
echo "  python -m training.train_yolo --data datasets/synthetic/dataset.yaml --epochs 50"
echo "  cp runs/electrical/weights/best.pt models/electrical_yolo.pt"
