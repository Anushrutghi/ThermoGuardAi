# ⚡ ThermoGuard AI

**Real-time Intelligent Electrical Panel Inspection, Fault Detection & Predictive Maintenance Platform**

A production-grade, modular Python platform for industrial electrical inspection.
It performs live component detection, thermal analysis, visible-fault recognition,
fault severity classification, intelligent alarms, predictive maintenance,
professional PDF reporting and complete inspection history — across webcams, USB
cameras, phone cameras (browser), IP/RTSP cameras, Raspberry Pi cameras, thermal
cameras (FLIR Lepton / MLX90640 / AMG8833 / Seek), uploaded images and videos.

---

## ✨ Highlights

| Capability | Status |
| --- | --- |
| Real-time detection pipeline (YOLOv11 + CV fallback) | ✅ Runs out of the box |
| RGB-only inspection mode (burn marks, sparks, smoke, corrosion…) | ✅ |
| Thermal analysis + simulator (no hardware needed) | ✅ Simulator default, drivers included |
| Mobile phone camera via browser (WebRTC / getUserMedia / WebSocket) | ✅ No app required |
| Fault severity: 🟢 Healthy / 🟡 Warning / 🟠 High / 🔴 Critical | ✅ |
| Intelligent alarms (sound, flashing frame, evidence capture, webhook) | ✅ |
| Predictive maintenance (trends, RUL, failure probability) | ✅ |
| Professional PDF reports (logo, tables, QR, signature) | ✅ |
| Digital panel mapping (stable component IDs) | ✅ |
| JWT auth + RBAC (admin / technician / viewer) | ✅ |
| Streamlit dashboard **and** React dashboard | ✅ |
| Training pipeline (synthetic dataset → YOLO → ONNX) | ✅ |
| Docker / Docker Compose deployment | ✅ |
| Unit + integration tests | ✅ |

---

## 🚀 Quick start (local)

Requires Python 3.12+ (3.12 recommended) and, optionally, `uv`.

```bash
# 1. Bootstrap environment (venv + deps + assets + seed data)
./scripts/setup.sh

# 2. Start everything (backend :8000 + dashboard :8501)
./scripts/start.sh --all
```

Open:
- **Dashboard (Streamlit):** http://localhost:8501
- **API docs:** http://localhost:8000/docs
- **Mobile phone camera client:** http://localhost:8000/mobile (open on your phone)

Default login (from `.env`): `admin` / `Admin123!`

> The API runs on port **8000** by default (override with `API_PORT` in `.env`).

### Alternative: Makefile

```bash
make setup        # create .venv + install core deps
make api          # uvicorn on :8000
make dashboard    # streamlit on :8501
make test         # run pytest
make assets       # generate alarm sound + logo
```

### Optional AI stack

```bash
./scripts/setup.sh --full        # adds torch + ultralytics + onnxruntime
./scripts/download_models.sh     # pretrained YOLO weights (generic)
```

---

## 🏗️ Architecture

```
Camera (webcam / USB / phone / IP / RTSP / Pi / file / thermal)
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  AI Pipeline (ai/pipeline.py)                                │
│  frame → preprocess → YOLO/CV detection → thermal mapping     │
│       → temperature estimation → fault classification         │
│       → risk analysis → predictive hints → annotated frame    │
└─────────────────────────────────────────────────────────────┘
   │
   ├──► FastAPI backend (backend/)  REST + WebSocket /ws/inspect
   ├──► Production data: Cloud Firestore + Firebase Storage (reports),
   │       Firebase Authentication (React). Local SQLite = dev/tests only.
   ├──► Alarm service — sound, webhook, evidence capture
   ├──► Reports (ReportLab PDF + QR → Firebase Storage in production)
   └──► Dashboards — Streamlit (frontend/streamlit) & React (frontend/react)
```

**Layers (clean architecture):**
- `backend/api` — HTTP/WS layer (routers, deps, schemas)
- `backend/services` — application services (inspection, auth, alarms, reports)
- `backend/repositories` — data access (Repository pattern)
- `backend/models` — SQLAlchemy ORM
- `ai/` — detection, thermal, visual faults, classification, predictive engine
- `camera/` — camera sources, capture manager, frame buffer
- `reports/` `analytics/` `training/` — reporting, analytics, model training

---

## 🎥 Camera support

| Source | How |
| --- | --- |
| Laptop/desktop webcam, USB | `POST /api/v1/cameras/switch` `{kind:"webcam", source:"0"}` |
| Mobile phone (Android/iPhone) | Open `/mobile` on the phone → Browser Camera API → frames stream over WebSocket |
| IP camera | MJPEG or RTSP URL: `{kind:"ip", source:"rtsp://user:pass@host/stream"}` |
| Raspberry Pi camera | `{kind:"rpi"}` (picamera2) |
| Uploaded images/videos | Streamlit Live page or `POST /inspections/{id}/frame` |
| Thermal (FLIR Lepton / MLX90640 / AMG8833 / Seek) | Auto-detected; falls back to a realistic **simulator** |

---

## ⚡ Adaptive performance engine (60 FPS on any hardware)

The pipeline self-tunes to the device it runs on — no manual configuration:

1. **Resolution scaling** — detection + visual CV heuristics run on a
   downscaled copy of the frame (bboxes are remapped back to full
   resolution). Scale ranges `PERF_MIN_SCALE=0.35` → `1.0`.
2. **Analysis stride** — on weak CPUs the full analysis runs at most every
   Nth frame (`PERF_MAX_STRIDE=8`); in between, cached results are re-used
   while every frame is still annotated and streamed. Live video stays
   smooth even when analysis is slow.
3. **Perf grade** — `ultra | high | mid | low` reported in every result;
   the transport layer adapts stream width / JPEG quality accordingly
   (low-grade devices get smaller, lighter frames).
4. **Backpressure streaming** — the mobile/React clients never pile up
   frames: they send the next frame only after the previous result arrives,
   racing toward `TARGET_FPS=60` on fast devices and backing off
   automatically on slow ones (with a 2s watchdog so streams can't stall).
5. **Decoupled capture** — camera capture runs at `CAPTURE_TARGET_FPS=60`
   in a background thread, so slow inference never blocks capture and
   vice-versa.

Benchmark any machine: `.venv/bin/python scripts/benchmark_fps.py --frames 240`

```text
Overall throughput: 28.7 FPS · Final tuning: scale=1.0 stride=1 grade=ultra   (fast device)
Overall throughput: 25.7 FPS · Final tuning: scale=0.35 stride=4 grade=high    (slow device)
```

## 🔬 AI engine

- **Detector:** `ai/detector/` — `YoloDetector` (Ultralytics YOLOv11) with graceful
  fallback to a CV rule-based detector when weights are absent (`DETECTOR_MODE=auto`).
- **Thermal:** `ai/thermal/` — statistics (max/min/avg/delta, hotspot, gradient, heat
  spread), colormap overlays, and drivers for real hardware + simulator.
- **Visual faults (RGB mode):** burn marks, sparks, smoke, water intrusion,
  discoloration, loose wires — pure OpenCV, works with any phone camera.
- **Fault classification:** `ai/fault/` — temperature-rise thresholds per component
  class (IEEE/NETA-style) → severity + recommendations.
- **Predictive maintenance:** `ai/predictive/` — trend fitting, remaining useful life,
  logistic failure probability, maintenance recommendations.

---

## 🧪 Tests

```bash
make test            # or: .venv/bin/python -m pytest tests -v
```

Covers security (JWT/bcrypt), detectors, thermal processing, fault classifier,
predictive engine, camera sources, and full API flows (auth → inspection → frame →
report → download).

---

## 🐳 Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

Runs the backend + Streamlit dashboard with the **Firebase production
architecture** (Firestore + Storage + Auth; no PostgreSQL/SQLite). Requires
`SECRET_KEY`, `CORS_ORIGINS`, `ALLOWED_HOSTS`, `SEED_ADMIN_*` and
`FIREBASE_SERVICE_ACCOUNT_FILE` (path to the backend-only service-account
JSON) via environment. Uses `DETECTOR_MODE=fallback` and
`THERMAL_MODE=simulator` by default.

---

## 🧠 Training a real model

```bash
python -m training.dataset_gen --samples 500        # synthetic panel images + labels
python -m training.train_yolo --data datasets/synthetic/dataset.yaml --epochs 80
python -m training.export_onnx --model runs/electrical/weights/best.pt
cp runs/electrical/weights/best.pt models/electrical_yolo.pt
```

---

## 📁 Project layout

```
backend/        FastAPI app (api, services, repositories, models, schemas)
ai/             Detection, thermal, visual faults, classification, predictive
camera/         Camera sources, capture manager, frame buffer
reports/        PDF generator, charts, QR codes
analytics/      Aggregations + Plotly charts
training/       Dataset generator, YOLO trainer, ONNX export
frontend/
  streamlit/    Initial dashboard (pages: Live, Analytics, History, Reports, Admin)
  react/        Professional React dashboard (Vite + TS)
  mobile/       Phone camera client page (getUserMedia + WebSocket)
config/         settings.yaml, classes.yaml
docker/         Dockerfile(s) + docker-compose
scripts/        setup.sh, start.sh, download_models.sh, generate_assets.py
tests/          Unit + integration tests
docs/           API contract
```

---

## 🔐 Environment variables

See `.env.example`. Key ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | `change-me…` | JWT signing (set a strong value in production) |
| `AUTH_BACKEND` | `local` | `local` (dev/tests) \| `firebase` (production) |
| `STORAGE_BACKEND` | `sqlite` | `sqlite` (dev/tests) \| `firestore` (production) |
| `FIREBASE_PROJECT_ID` | — | Firebase project (production: `thermoguardai`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Path to backend-only service-account JSON |
| `VITE_FIREBASE_*` | — | Public Firebase Web config for the React build |
| `DATABASE_URL` | `sqlite:///./database/thermoguard.db` | Dev/test only — never used in production |
| `DETECTOR_MODE` | `auto` | `auto` \| `yolo` \| `fallback` |
| `THERMAL_MODE` | `auto` | `auto` \| `simulator` \| `mlx90640` \| `lepton` \| `amg8833` \| `seek` |
| `MODEL_PATH` | `models/electrical_yolo.pt` | YOLO weights location |
| `CORS_ORIGINS` | localhost:8501,5173,3000 | Allowed dashboard origins |

**Production persistence is Firebase** (Cloud Firestore + Firebase Storage +
Firebase Authentication). Local SQLite is kept only for isolated development
and tests; production startup fails unless `AUTH_BACKEND=firebase` and
`STORAGE_BACKEND=firestore` are set, and it never falls back to SQLite. See
`docs/S6_FIREBASE_PLAN.md` and `docs/PRODUCTION.md`.

---

## 🗺️ Future expansion

The modular architecture (repository pattern, source abstractions, service layer)
is designed so IoT sensors, drone/robot inspection, SCADA/MQTT/OPC-UA connectors,
cloud sync, digital twins and multi-building monitoring can be added as new
`source`/`service`/`repository` modules without refactoring the core.

---

**Disclaimer:** ThermoGuard AI is an inspection-assistance tool. It does not
replace qualified electrical safety procedures, lockout/tagout, or certified
thermographic surveyors.
