#!/usr/bin/env bash
# ============================================================
# ThermoGuard AI — one-command startup (backend + dashboard)
#   ./scripts/start.sh            backend only (Streamlit optional)
#   ./scripts/start.sh --all      backend + Streamlit + mobile
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[[ -x "$PY" ]] || { echo "No .venv found. Run ./scripts/setup.sh first."; exit 1; }

export TG_API_BASE="${TG_API_BASE:-http://localhost:8000}"
MODE="${1:-api}"

echo "==> Starting ThermoGuard AI ($MODE)"

if [[ "$MODE" == "--all" ]]; then
  echo "   - FastAPI backend on  :8000  (mobile client: /mobile)"
  echo "   - Streamlit dashboard :8501"
  "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
  BACK_PID=$!
  trap "kill $BACK_PID 2>/dev/null || true" EXIT
  sleep 2
  "$PY" -m streamlit run frontend/streamlit/app.py --server.port 8501 --server.address 0.0.0.0
else
  exec "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port "${TG_API_PORT:-8000}"
fi
