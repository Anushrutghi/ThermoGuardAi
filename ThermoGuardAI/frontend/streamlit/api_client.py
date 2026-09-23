"""HTTP client for the Streamlit dashboard → FastAPI backend."""
from __future__ import annotations

import os
from typing import Any

import httpx

from backend.core.config import get_settings  # noqa: E402


def _lan_ip() -> str:
    """Best LAN address of this machine (for phone/mobile browser access)."""
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "localhost"


def get_api_base() -> str:
    """Resolve the backend base URL dynamically (the LAN IP can change)."""
    return os.getenv("TG_API_BASE", f"http://{_lan_ip()}:{get_settings().api_port}")


TIMEOUT = httpx.Timeout(30.0)


class ApiClient:
    """Thin wrapper around httpx with token injection."""

    def __init__(self, base: str | None = None, token: str | None = None) -> None:
        self._base_override = base
        self._token = token

    @property
    def base(self) -> str:
        """Backend base URL — refreshed on every access so IP changes never break it."""
        return (self._base_override or get_api_base()).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base}/api/v1{path}" if not path.startswith("http") else path

    # ------------------------------------------------------------------
    def get(self, path: str, **params: Any) -> Any:
        resp = httpx.get(self._url(path), params=params, headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, json: dict | None = None, data: dict | None = None, files: dict | None = None) -> Any:
        resp = httpx.post(self._url(path), json=json, data=data, files=files, headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        return self.post("/auth/login", json={"username": username, "password": password})

    def firebase_login(self, id_token: str) -> dict:
        """Exchange a Firebase ID token for the application session token.

        Production (AUTH_BACKEND=firebase) disables the local username/password
        login; the dashboard operator pastes a Firebase ID token (e.g. obtained
        from the React app's browser devtools or a Firebase Auth sign-in) which
        is exchanged server-side. Role/org come from the Firestore profile.
        """
        return self.post("/auth/firebase", json={"id_token": id_token})

    def health(self) -> dict:
        return httpx.get(f"{self.base}/health", timeout=10).json()

    def camera_status(self) -> dict:
        return self.get("/cameras")

    def switch_camera(self, kind: str, source: str | None = None) -> dict:
        return self.post("/cameras/switch", json={"id": kind, "name": kind, "kind": kind, "source": source})

    def start_inspection(self, mode: str = "manual", panel_id: int | None = None, camera_source: str = "webcam", notes: str | None = None) -> dict:
        return self.post(
            "/inspections",
            json={"mode": mode, "panel_id": panel_id, "camera_source": camera_source, "notes": notes},
        )

    def stop_inspection(self, inspection_id: int, notes: str | None = None) -> dict:
        return self.post(f"/inspections/{inspection_id}/stop", json={"notes": notes} if notes else {})

    def upload_frame(self, inspection_id: int, image_bytes: bytes, filename: str = "frame.jpg") -> dict:
        files = {"file": (filename, image_bytes, "image/jpeg")}
        return self.post(f"/inspections/{inspection_id}/frame", files=files)

    def list_inspections(
        self,
        search: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        panel_id: int | None = None,
        camera_source: str | None = None,
        archived: bool | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        sort: str = "started_at",
        order: str = "desc",
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """Filtered history query → {"items": [...], "total": N}."""
        params: dict[str, object] = {"sort": sort, "order": order, "limit": limit, "offset": offset}
        for key, value in (
            ("search", search),
            ("status", status),
            ("mode", mode),
            ("panel_id", panel_id),
            ("camera_source", camera_source),
            ("from_date", from_date),
            ("to_date", to_date),
        ):
            if value is not None and value != "":
                params[key] = value
        if archived is not None:
            params["archived"] = archived
        return self.get("/inspections", **params)

    def export_inspections(
        self,
        search: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        panel_id: int | None = None,
        archived: bool | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> bytes:
        """CSV export of the filtered history (raw bytes)."""
        params: dict[str, object] = {}
        for key, value in (
            ("search", search),
            ("status", status),
            ("mode", mode),
            ("panel_id", panel_id),
            ("from_date", from_date),
            ("to_date", to_date),
        ):
            if value is not None and value != "":
                params[key] = value
        if archived is not None:
            params["archived"] = archived
        url = self._url("/inspections/export")
        import httpx as _httpx

        resp = _httpx.get(url, params=params, headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content

    def get_inspection(self, inspection_id: int) -> dict:
        return self.get(f"/inspections/{inspection_id}")

    def archive_inspection(self, inspection_id: int) -> dict:
        return self.post(f"/inspections/{inspection_id}/archive", json={})

    def unarchive_inspection(self, inspection_id: int) -> dict:
        return self.post(f"/inspections/{inspection_id}/unarchive", json={})

    def delete_inspection(self, inspection_id: int) -> dict:
        resp = httpx.delete(self._url(f"/inspections/{inspection_id}"), headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # --- maintenance management -------------------------------------------
    def list_maintenance(self, status: str | None = None, priority: str | None = None) -> dict:
        params: dict[str, object] = {}
        if status:
            params["status"] = status
        if priority:
            params["priority"] = priority
        return self.get("/maintenance", **params)

    def create_maintenance(self, **fields) -> dict:
        return self.post("/maintenance", json=fields)

    def update_maintenance(self, record_id: int, **fields) -> dict:
        return self.post(f"/maintenance/{record_id}/update", json=fields)

    def delete_maintenance(self, record_id: int) -> dict:
        resp = httpx.delete(self._url(f"/maintenance/{record_id}"), headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # --- asset lifecycle management ---------------------------------------
    def asset_overview(self) -> dict:
        return self.get("/assets/overview")

    def asset_panel(self, panel_id: int) -> dict:
        return self.get(f"/assets/panels/{panel_id}")

    def asset_component(self, component_id: int) -> dict:
        return self.get(f"/assets/components/{component_id}")

    def asset_record_replacement(self, component_id: int) -> dict:
        return self.post(f"/assets/components/{component_id}/replace", json={})

    def asset_update_component(self, component_id: int, **fields) -> dict:
        return self.post(f"/assets/components/{component_id}/update", json=fields)

    def asset_retire_component(self, component_id: int) -> dict:
        return self.post(f"/assets/components/{component_id}/retire", json={})

    def asset_activate_component(self, component_id: int) -> dict:
        return self.post(f"/assets/components/{component_id}/activate", json={})

    def media_url(self, path: str | None) -> str | None:
        """Absolute URL for a stored media file (e.g. alarm evidence frame)."""
        if not path:
            return None
        if path.startswith("http"):
            return path
        return f"{self.base}/media/{path.split('media/')[-1]}"

    def inspection_stats(self) -> dict:
        return self.get("/inspections/stats")

    def list_panels(self) -> list[dict]:
        return self.get("/panels")

    def create_panel(self, name: str, code: str, location: str | None = None, description: str | None = None) -> dict:
        return self.post("/panels", json={"name": name, "code": code, "location": location, "description": description})

    def analytics_summary(self, days: int = 30) -> dict:
        return self.get("/analytics/summary", days=days)

    def analytics_inspections(self, days: int = 30) -> dict:
        return self.get("/analytics/inspections", days=days)

    def analytics_temps(self, days: int = 30) -> dict:
        return self.get("/analytics/temperatures", days=days)

    def analytics_health(self) -> dict:
        return self.get("/analytics/component-health")

    def predictive(self, panel_id: int | None = None) -> dict:
        return self.get("/analytics/predictive", panel_id=panel_id or None)

    def list_alarms(self, open_only: bool = False) -> list[dict]:
        return self.get("/alarms", open_only=open_only)

    def acknowledge_alarm(self, alarm_id: int) -> dict:
        return self.post(f"/alarms/{alarm_id}/acknowledge", json={})

    def list_reports(self) -> list[dict]:
        return self.get("/reports")

    def download_report(self, report_id: int) -> bytes:
        resp = httpx.get(self._url(f"/reports/{report_id}/download"), headers=self._headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content

    def generate_report(self, inspection_id: int, title: str | None = None, notes: str | None = None) -> dict:
        return self.post(f"/reports/inspections/{inspection_id}", json={"title": title, "notes": notes} if (title or notes) else {})

    def report_url(self, report_id: int) -> str:
        """Absolute download URL. Plain browser navigation (st.link_button)
        cannot send an Authorization header, so the JWT is appended as a query
        param — the endpoint accepts both Bearer and ?token=."""
        url = f"{self.base}/api/v1/reports/{report_id}/download"
        if self._token:
            url += f"?token={self._token}"
        return url
