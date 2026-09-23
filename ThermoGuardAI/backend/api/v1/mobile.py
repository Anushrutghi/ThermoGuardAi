"""Mobile browser client page route."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["mobile"])

_MOBILE_HTML = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "mobile" / "index.html"


def serve_mobile_client() -> FileResponse:
    """Serve the phone camera client page."""
    return FileResponse(str(_MOBILE_HTML), media_type="text/html")


@router.get("/mobile")
def mobile_client() -> FileResponse:
    """Legacy v1-prefixed alias (http://host/api/v1/mobile)."""
    return serve_mobile_client()
