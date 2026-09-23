"""QR code generation for report traceability."""
from __future__ import annotations

import logging
from pathlib import Path

import qrcode

logger = logging.getLogger(__name__)


def make_qr(data: str, output_path: str | Path, box_size: int = 6, border: int = 2) -> Path:
    """Generate a QR code PNG embedding `data` (e.g. report verification URL)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0E4DA4", back_color="white")
    img.save(out)
    return out
