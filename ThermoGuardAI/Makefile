.PHONY: setup setup-ai install dev api dashboard test lint format seed assets clean

PYTHON ?= .venv/bin/python
UV ?= uv

## Bootstrap a local environment (fast, no heavy AI stack)
setup:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install --python .venv/bin/python -r requirements-dev.txt

## Full install including PyTorch / Ultralytics / ONNX
setup-ai: setup
	$(UV) pip install --python .venv/bin/python -r requirements-ai.txt

install:
	$(UV) pip install --python .venv/bin/python -r requirements-dev.txt

## Run the FastAPI backend
api:
	$(PYTHON) -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

## Run the Streamlit dashboard
dashboard:
	$(PYTHON) -m streamlit run frontend/streamlit/app.py --server.port 8501

## One-command startup (backend + dashboard)
dev: api

## Tests
test:
	$(PYTHON) -m pytest tests -v

## Lint / format
lint:
	$(UV) run --python .venv/bin/python ruff check backend ai camera reports analytics training tests frontend/streamlit

format:
	$(UV) run --python .venv/bin/python ruff format backend ai camera reports analytics training tests frontend/streamlit

## Seed database with admin user + demo panel
seed:
	$(PYTHON) -m backend.db.seed

## Generate runtime assets (alarm sound, logo)
assets:
	$(PYTHON) scripts/generate_assets.py

clean:
	rm -rf .venv .pytest_cache .ruff_cache database/*.db logs/*.log reports/generated media/uploads __pycache__
