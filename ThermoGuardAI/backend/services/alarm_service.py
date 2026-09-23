"""Alarm service — triggers intelligent alarms on critical faults.

Actions: flash bounding box (rendered by clients), play sound, create
alarm record, save annotated frame, optional webhook.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.alarm import Alarm
from backend.repositories.alarm_repo import AlarmRepository
from backend.repositories.event_repo import EventLogRepository

logger = logging.getLogger(__name__)


class AlarmService:
    """Handles alarm creation and notifications."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.alarms = AlarmRepository(db)
        self.events = EventLogRepository(db)

    # ------------------------------------------------------------------
    def raise_alarm(
        self,
        message: str,
        severity: str = "critical",
        inspection_id: int | None = None,
        source: str = "system",
        media_path: str | None = None,
        payload: dict | None = None,
        user_id: int | None = None,
        sound: bool = True,
    ) -> Alarm:
        """Persist an alarm and fire notifications.

        `sound` controls whether the audible buzzer is played. Only confirmed
        circuit-overheating (deep heat verification) may sound the buzzer —
        every other alarm is recorded silently.
        """
        alarm = self.alarms.create(
            inspection_id=inspection_id,
            severity=severity,
            message=message,
            source=source,
            media_path=media_path,
            payload_json=json.dumps(payload) if payload else None,
        )
        self.events.log("warning", "alarm", f"Alarm raised: {message}", user_id=user_id, details=payload)
        self.db.commit()

        self._notify_async(message, severity, payload, sound)
        return alarm

    def acknowledge(self, alarm_id: int, by: str | None = None) -> Alarm:
        alarm = self.alarms.get_or_raise(alarm_id)
        self.alarms.update(
            alarm,
            acknowledged=True,
            acknowledged_at=datetime.now(UTC),
            acknowledged_by=by,
        )
        self.db.commit()
        return alarm

    def list_open(self, limit: int = 50, scope=None) -> list[Alarm]:  # noqa: ANN001
        return self.alarms.list_open(limit, scope=scope)

    # ------------------------------------------------------------------
    def _notify_async(self, message: str, severity: str, payload: dict | None, sound: bool) -> None:
        """Fire sound + webhook in a background thread (non-blocking)."""
        threading.Thread(target=self._notify, args=(message, severity, payload, sound), daemon=True).start()

    def _notify(self, message: str, severity: str, payload: dict | None, sound: bool) -> None:
        settings = get_settings()
        try:
            if sound and severity in ("critical", "high"):
                self._play_sound(settings.alarm_sound_full_path)
        except Exception:
            logger.exception("Alarm sound failed")
        if settings.alarm_webhook_url:
            self._send_webhook(settings.alarm_webhook_url, message, severity, payload)

    @staticmethod
    def _play_sound(path: Path) -> None:
        import os
        import platform
        import subprocess

        if not path.exists():
            return
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Linux":
            subprocess.Popen(["aplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            os.startfile(str(path))  # noqa: S606

    @staticmethod
    def _send_webhook(url: str, message: str, severity: str, payload: dict | None) -> None:
        import urllib.request

        data = json.dumps({"severity": severity, "message": message, "payload": payload}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5):  # noqa: S310
            pass
