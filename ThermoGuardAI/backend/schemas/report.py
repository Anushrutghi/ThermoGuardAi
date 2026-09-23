"""Report schemas (S4: no internal filesystem paths in responses).

``ReportOut`` intentionally omits ``file_path`` (the server-side file location
stays server-side) and exposes only safe metadata: id, title, ``file_name``,
risk score, author, timestamps and a boolean ``file_available`` (never a path).
Downloads continue through the authorized ``/reports/{id}/download`` endpoint,
which resolves the file server-side.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.schemas.common import ORMModel


class ReportOut(ORMModel):
    id: int
    inspection_id: int | None = None
    title: str
    file_name: str | None = None
    # S5: availability is a boolean — the server-side filesystem path never
    # leaves the API (prefer over exposing any internal path).
    file_available: bool = False
    risk_score: float
    generated_by: str | None = None
    generated_at: datetime
    report_type: str
    snapshot_sha256: str | None = None
    snapshot_version: str | None = None


class ReportRequest(BaseModel):
    title: str | None = None
    notes: str | None = None
