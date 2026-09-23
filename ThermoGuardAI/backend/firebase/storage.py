"""Firebase Storage-backed report files (S6).

Report PDFs live in the Firebase Storage bucket under ``reports/{id}.pdf``.
There are NO public or signed URLs: the API returns ``file_available: bool``
and serves file bytes through the authenticated, organization-authorized
download endpoint only. Server filesystem paths are never exposed.
"""
from __future__ import annotations

import logging

from backend.core.exceptions import NotFoundError

logger = logging.getLogger(__name__)

REPORT_PREFIX = "reports"


def _bucket():
    from backend.firebase.client import get_storage_bucket

    return get_storage_bucket()


def upload_report_pdf(report_id: str, data: bytes) -> str:
    """Upload report bytes; returns the remote blob path."""
    name = f"{REPORT_PREFIX}/{report_id}.pdf"
    blob = _bucket().blob(name)
    blob.upload_from_string(data, content_type="application/pdf")
    logger.info("Report PDF uploaded to Firebase Storage: %s", name)
    return name


def report_file_exists(remote_path: str) -> bool:
    """True when the report blob exists (never raises)."""
    if not remote_path:
        return False
    try:
        return bool(_bucket().blob(remote_path).exists())
    except Exception:  # noqa: BLE001 — availability must not crash listing
        logger.warning("Could not check report blob %s", remote_path)
        return False


def download_report_pdf(remote_path: str) -> bytes:
    """Stream a report blob (authorized by the caller)."""
    blob = _bucket().blob(remote_path)
    if not blob.exists():
        raise NotFoundError("Report file missing in storage")
    return blob.download_as_bytes()


def delete_report_pdf(remote_path: str) -> None:
    """Best-effort blob removal."""
    try:
        _bucket().blob(remote_path).delete()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to delete report blob %s", remote_path)
