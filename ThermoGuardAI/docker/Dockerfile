# ============================================================
# ThermoGuard AI — backend image
# Build:  docker build -f docker/Dockerfile -t thermoguard:latest .
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# System deps for OpenCV / camera I/O
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps (split: core first, then optional AI)
COPY requirements.txt requirements-ai.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    || pip install --no-cache-dir -r requirements.txt --no-deps

# App code
COPY backend ./backend
COPY ai ./ai
COPY camera ./camera
COPY reports ./reports
COPY analytics ./analytics
COPY config ./config
COPY frontend/mobile ./frontend/mobile
COPY assets ./assets
COPY scripts ./scripts

# Runtime dirs. NOTE: no `database` dir — production persistence is
# Cloud Firestore + Firebase Storage, never a local database. `reports/generated`
# is only a transient working directory (PDFs are uploaded to Firebase Storage
# and the temp copy removed).
RUN mkdir -p logs models media reports/generated datasets/generated

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
