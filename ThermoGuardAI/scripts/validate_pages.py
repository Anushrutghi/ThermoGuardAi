"""Validate the rewritten Streamlit pages execute without runtime errors.

Uses Streamlit's AppTest harness with a real API token against the running
backend (the port comes from config/.env, default 8000).
Run: .venv/bin/python scripts/validate_pages.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from backend.core.config import get_settings  # noqa: E402
from frontend.streamlit.api_client import ApiClient  # noqa: E402


def _token() -> str:
    settings = get_settings()
    resp = httpx.post(
        f"http://localhost:{settings.api_port}/api/v1/auth/login",
        json={"username": settings.seed_admin_username, "password": settings.seed_admin_password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def check_page(script: str, token: str) -> None:
    at = AppTest.from_file(str(ROOT / script))
    at.session_state["token"] = token
    at.session_state["api"] = ApiClient(token=token)
    at.run(timeout=60)
    errors = [str(e.value) for e in at.exception]
    print(f"[{script}] exceptions: {errors or 'none'}")
    print(f"[{script}] titles: {[t.value for t in at.title]}")
    print(f"[{script}] dataframes: {len(at.dataframe)}, metrics: {len(at.metric)}, buttons: {len(at.button)}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    token = _token()
    check_page("frontend/streamlit/pages/3_Inspection_History.py", token)
    check_page("frontend/streamlit/pages/6_Maintenance.py", token)
    check_page("frontend/streamlit/pages/7_Asset_Lifecycle.py", token)
    print("✅ All Streamlit pages validated")
