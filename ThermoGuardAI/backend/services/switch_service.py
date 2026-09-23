"""Switch-first inspection persistence (S1).

Once the switch controller completes its scan, this service persists the
evidence into the inspection record: the confirmed switch detection, the
temperature time-series, the anomaly classification (as a fault + alarm when
warranted) and evidence screenshots. All rows are scoped to the inspection's
own device — nothing ever mixes across devices.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.detector.base import HealthStatus
from ai.switch.thermal import ABNORMAL, CRITICAL, ELEVATED, NORMAL
from backend.core.config import get_settings
from backend.models.detection import Detection
from backend.models.fault import Fault as FaultModel
from backend.models.inspection import Inspection
from backend.models.temperature_history import TemperatureReading
from backend.models.thermal_history import ThermalHistory
from backend.repositories.inspection_repo import InspectionRepository
from backend.services.alarm_service import AlarmService

logger = logging.getLogger(__name__)

# Conservative classification → fault mapping. NORMAL never creates a fault.
_CLASS_TO_FAULT: dict[str, tuple[HealthStatus, str]] = {
    ELEVATED: (HealthStatus.WARNING, "elevated_temperature"),
    ABNORMAL: (HealthStatus.HIGH_RISK, "thermal_anomaly"),
    CRITICAL: (HealthStatus.CRITICAL, "critical_thermal_anomaly"),
}


def _recommendation(classification: str) -> str:
    if classification == CRITICAL:
        return (
            "Immediate professional electrical inspection recommended. Do not operate the switch "
            "until it has been inspected by a qualified electrician."
        )
    if classification == ABNORMAL:
        return "Further professional electrical inspection recommended — monitor the switch and its load."
    if classification == ELEVATED:
        return "Monitor the switch; re-inspect if the thermal pattern persists or worsens."
    return "No action required."


class SwitchInspectionService:
    """Persistence for the switch-first workflow."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.inspections = InspectionRepository(db)
        self.alarm_service = AlarmService(db)

    # ------------------------------------------------------------------
    def persist_scan_sample(self, inspection: Inspection, status: dict) -> None:
        """Record one temperature reading per scan sample (bounded by design)."""
        sample = status.get("scan_sample")
        if not sample or not status.get("thermal_available"):
            return
        self.db.add(
            TemperatureReading(
                component_id=None,
                component_label="wall_switch",
                inspection_id=inspection.id,
                temperature=sample.get("switch"),
                reading_time=datetime.now(UTC),
                source="thermal",
            )
        )
        self.db.commit()

    # ------------------------------------------------------------------
    def finalize(
        self,
        inspection: Inspection,
        status: dict,
        frame: np.ndarray,
        annotated: np.ndarray,
        thermal_frame=None,  # noqa: ANN001 — optional raw thermal frame for the heat map
    ) -> Inspection:
        """Persist the full switch-scan result into the inspection record.

        Idempotent: a completed scan is never persisted twice (a WS reconnect
        or duplicate finalize call cannot duplicate detections/faults/history).
        """
        existing_finalized = self.db.scalar(
            select(Detection.id).where(Detection.inspection_id == inspection.id, Detection.label == "wall_switch")
        )
        if existing_finalized:
            return inspection
        complete = status.get("complete") or {}
        classification = complete.get("classification") or NORMAL
        switch_temp = complete.get("switch_temp")

        # 1) the confirmed switch detection
        bbox = status.get("switch_bbox")
        if bbox:
            severity = _CLASS_TO_FAULT.get(classification, (HealthStatus.HEALTHY, ""))[0]
            self.db.add(
                Detection(
                    inspection_id=inspection.id,
                    component_id=None,
                    label="wall_switch",
                    confidence=float(status.get("switch_confidence") or 0.0),
                    bbox=json.dumps([int(v) for v in bbox]),
                    temperature=switch_temp,
                    health=severity.value,
                    frame_index=inspection.frames_processed,
                )
            )

        # 2) the temperature time-series
        for s in complete.get("series", []):
            self.db.add(
                TemperatureReading(
                    component_id=None,
                    component_label="wall_switch",
                    inspection_id=inspection.id,
                    temperature=s.get("switch"),
                    reading_time=datetime.now(UTC),
                    source="thermal",
                )
            )

        # 3) the anomaly classification as a fault (only when warranted)
        fault_type: str | None = None
        severity: HealthStatus | None = None
        if classification in _CLASS_TO_FAULT and switch_temp is not None:
            severity, fault_type = _CLASS_TO_FAULT[classification]
            self.db.add(
                FaultModel(
                    inspection_id=inspection.id,
                    component_id=None,
                    component_label="wall_switch",
                    fault_type=fault_type,
                    severity=severity.value,
                    confidence=0.9,
                    temperature=switch_temp,
                    message=complete.get("message"),
                    recommendation=_recommendation(classification),
                )
            )

        # 4) evidence screenshots (original + annotated + thermal heat map)
        original_path, annotated_path, thermal_path = self._save_evidence(
            inspection, frame, annotated, thermal_frame
        )

        # 5) alarm for abnormal/critical findings (single, at completion)
        if classification in (ABNORMAL, CRITICAL) and severity is not None:
            self.alarm_service.raise_alarm(
                message=complete.get("message") or "Thermal anomaly detected near electrical switch",
                severity=severity.value,
                inspection_id=inspection.id,
                source="switch-first",
                media_path=annotated_path,
                payload={
                    "fault_type": fault_type,
                    "temperature": switch_temp,
                    "classification": classification,
                    "evidence": complete.get("evidence", []),
                },
                user_id=inspection.user_id,
                sound=classification == CRITICAL,
            )

        # 6) inspection record fields (status flips to completed on stop)
        self.inspections.update(
            inspection,
            risk_score=float(complete.get("risk_score") or 0.0),
            component_count=1,
            thermal_source=status.get("thermal_source") or inspection.thermal_source,
            thermal_simulated=bool(status.get("thermal_simulated") or inspection.thermal_simulated),
            model_version="cv-switch",
            original_image_path=original_path,
            annotated_image_path=annotated_path,
            thermal_image_path=thermal_path,
        )

        # 7) S2 per-inspection thermal summary (one row per completed switch
        # scan, written from the real ThermalScanResult — never fabricated).
        # DEMO/simulated sources are stored WITH their simulated flag so real
        # analytics can always exclude them.
        if complete.get("thermal_available"):
            existing = self.db.scalar(
                select(ThermalHistory).where(ThermalHistory.inspection_id == inspection.id)
            )
            hotspot = complete.get("hotspot") or {}
            values = dict(
                device_id=inspection.device_id,
                timestamp=datetime.now(UTC),
                max_temp=complete.get("max_temp"),
                min_temp=complete.get("min_temp"),
                avg_temp=complete.get("avg_temp"),
                reference_temp=complete.get("wall_temp"),
                delta=complete.get("delta_vs_wall"),
                hotspot_x=hotspot.get("x") if isinstance(hotspot, dict) else None,
                hotspot_y=hotspot.get("y") if isinstance(hotspot, dict) else None,
                trend_c_per_min=complete.get("trend_c_per_min"),
                trend_delta_c=complete.get("trend_delta_c"),
                rapid_increase=bool(complete.get("rapid_increase")),
                heat_path=complete.get("heat_path"),
                classification=classification,
                # The active session source is authoritative (never the global
                # singleton): emissivity is recorded from the scan result that
                # the injected source produced.
                emissivity=complete.get("emissivity"),
                thermal_source=status.get("thermal_source") or inspection.thermal_source,
                mode=inspection.mode,
                simulated=bool(status.get("thermal_simulated") or inspection.thermal_simulated),
            )
            if existing is None:
                self.db.add(ThermalHistory(inspection_id=inspection.id, **values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
        self.db.commit()

        # 8) refresh the derived operational status from the fresh evidence
        if inspection.device_id:
            try:
                from backend.models.device import Device as DeviceModel
                from backend.services.device_analytics import DeviceAnalyticsService

                device = self.db.get(DeviceModel, inspection.device_id)
                if device is not None:
                    DeviceAnalyticsService(self.db).refresh_derived_status(device)
                    self.db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to refresh device status after switch inspection")
        logger.info(
            "Switch inspection %s finalized: classification=%s risk=%.1f",
            inspection.inspection_code,
            classification,
            complete.get("risk_score") or 0.0,
        )
        return inspection

    # ------------------------------------------------------------------
    def _save_evidence(
        self,
        inspection: Inspection,
        frame: np.ndarray,
        annotated: np.ndarray,
        thermal_frame=None,  # noqa: ANN001
    ) -> tuple[str | None, str | None, str | None]:
        """Persist evidence screenshots for the report (best-effort)."""
        try:
            import cv2

            from ai.thermal.processing import overlay_on_rgb, to_colormap

            frames_dir = self.settings.media_path / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            base = f"switch_insp{inspection.id}_{int(time.time())}"
            original = frames_dir / f"{base}_original.jpg"
            annotated_f = frames_dir / f"{base}_annotated.jpg"
            thermal_f = frames_dir / f"{base}_thermal.jpg"

            original_path = annotated_path = thermal_path = None
            for path, img in ((original, frame), (annotated_f, annotated)):
                ok, buf = cv2.imencode(".jpg", img)
                if ok:
                    path.write_bytes(buf.tobytes())
            original_path, annotated_path = str(original), str(annotated_f)

            if thermal_frame is not None and getattr(thermal_frame, "temperatures", None) is not None:
                overlay = overlay_on_rgb(frame.copy(), to_colormap(thermal_frame))
                ok, buf = cv2.imencode(".jpg", overlay)
                if ok:
                    thermal_f.write_bytes(buf.tobytes())
                    thermal_path = str(thermal_f)
            return original_path, annotated_path, thermal_path
        except Exception:  # noqa: BLE001
            logger.exception("Failed to save switch inspection evidence")
            return None, None, None
