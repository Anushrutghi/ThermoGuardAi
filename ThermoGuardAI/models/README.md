# Models

This directory holds trained YOLO weights (`.pt` / `.onnx`), which are
git-ignored.

| File | Purpose |
| --- | --- |
| `electrical_yolo.pt` | Trained YOLO model for electrical components (expected path from `.env`) |
| `electrical_yolo.onnx` | ONNX export for edge/Jetson deployment |

**Out of the box**, the platform runs with the **CV fallback detector**
(no weights needed). To enable neural detection:

```bash
# Option A — generic pretrained model (COCO, not component-specific)
./scripts/download_models.sh yolo11n.pt

# Option B — train a component-specific model
python -m training.dataset_gen --samples 500        # synthetic dataset
python -m training.train_yolo --data datasets/synthetic/dataset.yaml --epochs 80
cp runs/electrical/weights/best.pt models/electrical_yolo.pt
```

Then set `MODEL_PATH=models/electrical_yolo.pt` and `DETECTOR_MODE=auto` in `.env`.
