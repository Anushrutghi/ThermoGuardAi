#!/usr/bin/env bash
# ============================================================
# ThermoGuard AI — one-command local setup
#   ./scripts/setup.sh            (core install)
#   ./scripts/setup.sh --full     (adds PyTorch/Ultralytics AI stack)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
FULL=0
if [[ "${1:-}" == "--full" ]]; then FULL=1; fi

echo "==> Creating virtual environment (.venv) with ${PYTHON_BIN}"
command -v uv >/dev/null 2>&1 && UV=uv || UV=""
if [[ -n "$UV" ]]; then
  $UV venv --python "$PYTHON_BIN" .venv
  $UV pip install --python .venv/bin/python -r requirements-dev.txt
  if [[ $FULL -eq 1 ]]; then
    $UV pip install --python .venv/bin/python -r requirements-ai.txt
  fi
else
  "$PYTHON_BIN" -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements-dev.txt
  if [[ $FULL -eq 1 ]]; then
    ./.venv/bin/pip install -r requirements-ai.txt
  fi
fi

echo "==> Generating runtime assets (alarm sound, logo)"
./.venv/bin/python scripts/generate_assets.py

echo "==> Seeding database (admin user + demo panel)"
./.venv/bin/python -m backend.db.seed || true

echo
echo "✅ Setup complete."
echo "   Start the backend:   ./scripts/start.sh"
echo "   Or: make api         (backend)   make dashboard (Streamlit)"
if [[ $FULL -eq 0 ]]; then
  echo "   Install AI stack later: ./scripts/setup.sh --full"
fi
