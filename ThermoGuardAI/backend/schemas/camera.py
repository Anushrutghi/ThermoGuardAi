"""Camera source schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CameraKind = Literal["webcam", "usb", "file", "video", "ip", "rtsp", "mjpeg", "phone", "rpi", "thermal"]


class CameraSourceOut(BaseModel):
    id: str
    name: str
    kind: CameraKind
    status: str = "unknown"  # available | unavailable | error
    fps: float | None = None
    width: int | None = None
    height: int | None = None


class CameraCreate(BaseModel):
    id: str
    name: str
    kind: CameraKind
    source: str | None = Field(default=None, description="URL / path / index, e.g. rtsp://... or 0")


class CameraList(BaseModel):
    items: list[CameraSourceOut]
    active_id: str | None = None
