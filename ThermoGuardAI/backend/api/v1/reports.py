"""Report routes (org-isolated, S2.1).

Ownership chain: Organization → Device → Inspection → Report.
Organization is derived through the inspection's device — never duplicated.
Unowned legacy reports (no inspection, or device-less inspection) stay
accessible to all authenticated users; device-linked reports are strictly
scoped to their organization. Authorization always happens BEFORE a report
file is returned, so a direct-ID guess or bare download URL can never bypass
isolation.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.api.deps import bearer_scheme, decode_and_get_user, get_current_user, require_roles
from backend.core.config import get_settings
from backend.core.exceptions import NotFoundError, UnauthorizedError
from backend.db.session import get_db
from backend.models.user import User
from backend.schemas.report import ReportOut, ReportRequest
from backend.services.isolation import ensure_inspection_for_organization, ensure_report_accessible
from backend.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])



@router.post("/inspections/{inspection_id}", response_model=ReportOut, status_code=201)
def generate_report(
    inspection_id: int,
    payload: ReportRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> ReportOut:
    # A report referencing another organization's inspection is never
    # generatable — the cross-org inspection looks like a not-found inspection.
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    report = ReportService(db).generate_for_inspection(
        inspection_id,
        title=payload.title if payload else None,
        notes=payload.notes if payload else None,
    )
    return ReportOut.model_validate(report)


@router.get("", response_model=list[ReportOut])
def list_reports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # noqa: B008
) -> list[ReportOut]:
    return [
        ReportOut.model_validate(r)
        for r in ReportService(db).reports.list_scoped(user.organization, limit=100)
    ]


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # noqa: B008
) -> ReportOut:
    """Single report lookup (org-scoped; 404 for other organizations)."""
    report = ensure_report_accessible(db, report_id, user.organization)
    return ReportOut.model_validate(report)


@router.get("/{report_id}/download")
def download_report(
    report_id: int,
    token: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> FileResponse:
    # Accept the JWT from the Authorization header OR a ?token= query param:
    # dashboards send the Bearer header; plain <a>/link_button downloads cannot
    # set headers (same pattern as /cameras/frame?token=).
    raw = credentials.credentials if credentials else token
    if not raw:
        raise UnauthorizedError("Not authenticated")
    user = decode_and_get_user(raw, db)
    # Org authorization BEFORE the file is returned — a direct ID guess, a
    # foreign token, or a bare download URL cannot reveal another org's report.
    report = ensure_report_accessible(db, report_id, user.organization)
    if get_settings().storage_uses_firestore:
        # S6: report files live in Firebase Storage. Authorization already
        # happened above; bytes are streamed through this authenticated,
        # org-scoped endpoint — no public/signed URL is ever issued.
        from fastapi.responses import Response

        from backend.firebase.storage import download_report_pdf

        data = download_report_pdf(report.file_path)  # NotFoundError when missing
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{Path(report.file_path).name}"',
                "Cache-Control": "no-store",
            },
        )
    path = Path(report.file_path)
    if not path.exists():
        raise NotFoundError("Report file missing on disk")
    # no-store: the URL may carry a token — never let proxies/browsers cache it.
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{report_id}/verify")
def verify_report(
    report_id: int,
    hash: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Public verification endpoint for QR code document tracing."""
    from backend.models.report import Report

    report = db.get(Report, report_id)
    if report is None:
        raise NotFoundError(f"Report {report_id} not found")
    hash_matches = True
    if hash and report.snapshot_sha256:
        hash_matches = report.snapshot_sha256.startswith(hash)
    return {
        "report_id": report.id,
        "title": report.title,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        "verified": bool(report.snapshot_sha256 and hash_matches),
        "snapshot_sha256": report.snapshot_sha256,
        "disclaimer": (
            "Verification confirms document integrity and generation by ThermoGuard AI. "
            "It does NOT certify electrical equipment safety or standards compliance."
        ),
    }


@router.get("/{report_id}/snapshot")
def get_report_snapshot(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Authenticated endpoint returning the frozen immutable snapshot for an inspection."""
    import json

    report = ensure_report_accessible(db, report_id, user.organization)
    if not report.snapshot_json:
        raise NotFoundError(f"Snapshot not found for report {report_id}")
    return json.loads(report.snapshot_json)

