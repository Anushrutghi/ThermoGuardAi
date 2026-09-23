"""Inspection service — orchestrates live pipeline runs and persistence."""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.detector.base import HealthStatus
from ai.fault.types import Fault
from ai.pipeline import InspectionPipeline
from backend import __version__
from backend.core.config import get_settings
from backend.core.exceptions import NotFoundError
from backend.models.component import Component
from backend.models.detection import Detection
from backend.models.fault import Fault as FaultModel
from backend.models.incident import Incident
from backend.models.inspection import Inspection
from backend.models.maintenance import MaintenanceRecord
from backend.models.temperature_history import TemperatureReading
from backend.repositories.component_repo import ComponentRepository
from backend.repositories.event_repo import EventLogRepository
from backend.repositories.inspection_repo import InspectionRepository
from backend.repositories.panel_repo import PanelRepository
from backend.services.alarm_service import AlarmService

logger = logging.getLogger(__name__)

_pipeline: InspectionPipeline | None = None
_pipeline_lock = __import__("threading").Lock()

# Persist detection details at most every N frames (continuous mode generates
# thousands of frames; sampling keeps the DB bounded while trends remain valid)
DETECTION_SAMPLE_EVERY = 10
# Cooldown between alarms for the same fault type + component (seconds)
ALARM_COOLDOWN_S = 30.0
# Default deadline (days) for maintenance records created from critical faults
MAINTENANCE_DEADLINE_DAYS = 7
# Capture evidence screenshots at most every N frames when nothing is critical
EVIDENCE_EVERY = 10


def get_pipeline() -> InspectionPipeline:
    """Singleton pipeline shared across requests."""
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            _pipeline = InspectionPipeline()
        return _pipeline


def _model_version() -> str:
    """Human-readable detector/model version for the current runtime."""
    try:
        from ai.detector.factory import get_detector

        detector = get_detector()
        engine = detector.engine
        settings = get_settings()
        if engine != "cv-fallback" and settings.model_full_path.exists():
            return f"{engine}:{settings.model_full_path.name}"
        return engine
    except Exception:  # noqa: BLE001
        logger.exception("Could not resolve model version")
        return "unknown"


class InspectionService:
    """Handles inspection lifecycle: start, process frame, persist, stop."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.inspections = InspectionRepository(db)
        self.panels = PanelRepository(db)
        self.components = ComponentRepository(db)
        self.events = EventLogRepository(db)
        self.alarm_service = AlarmService(db)
        self.pipeline = get_pipeline()
        self._last_alarm: dict[str, float] = {}

    # ------------------------------------------------------------------
    def _next_inspection_code(self, now: datetime) -> str:
        """TG-YYYYMMDD-NNNNN with a per-day sequence, collision-safe."""
        seq = self.inspections.count_started_on(now) + 1
        for _ in range(100):
            code = f"TG-{now:%Y%m%d}-{seq:05d}"
            taken = self.db.scalar(select(Inspection.id).where(Inspection.inspection_code == code))
            if not taken:
                return code
            seq += 1
        return f"TG-{now:%Y%m%d}-{seq:05d}"

    def start_inspection(
        self,
        user_id: int | None,
        mode: str = "manual",
        panel_id: int | None = None,
        device_id: int | None = None,
        camera_source: str = "webcam",
        notes: str | None = None,
        organization: str | None = None,
    ) -> Inspection:
        now = datetime.now(UTC)
        # Thermal honesty: record which source produced temperatures so the
        # inspection record and generated reports can label simulated data DEMO.
        from ai.thermal.factory import get_thermal_source

        thermal = get_thermal_source()
        if device_id is not None:
            # Every inspection must belong to exactly one device (S1) and may
            # only be started by a user of the same organization (S2 isolation).
            from backend.models.device import Device

            device = self.db.get(Device, device_id)
            if device is None:
                raise NotFoundError(f"Device {device_id} not found")
            # S2 isolation (strict, NULL matches NULL): only users of the
            # device's organization may start an inspection against it.
            if device.organization != organization:
                raise NotFoundError(f"Device {device_id} not found")
        if panel_id is not None:
            # Panels carry optional organization ownership (S4). Legacy panels
            # (NULL organization) stay usable by everyone; org-scoped panels are
            # only usable by their own organization — never silently bypassed.
            from backend.models.panel import Panel

            panel = self.db.get(Panel, panel_id)
            if panel is None:
                raise NotFoundError(f"Panel {panel_id} not found")
            if panel.organization is not None and panel.organization != organization:
                raise NotFoundError(f"Panel {panel_id} not found")
        inspection = self.inspections.create(
            user_id=user_id,
            panel_id=panel_id,
            device_id=device_id,
            mode=mode,
            camera_source=camera_source,
            status="running",
            started_at=now,
            notes=notes,
            inspection_code=self._next_inspection_code(now),
            software_version=__version__,
            model_version=_model_version(),
            thermal_source=thermal.name,
            thermal_simulated=bool(getattr(thermal, "simulated", False)),
        )
        self.db.commit()
        self.events.log("info", "inspection", f"Inspection {inspection.inspection_code} started ({mode})", user_id=user_id)
        self.db.commit()
        logger.info(
            "Inspection %s started (id=%s mode=%s device=%s panel=%s thermal_source=%s simulated=%s)",
            inspection.inspection_code,
            inspection.id,
            mode,
            device_id,
            panel_id,
            thermal.name,
            bool(getattr(thermal, "simulated", False)),
        )
        return inspection

    # ------------------------------------------------------------------
    def process_frame(self, inspection_id: int, frame: np.ndarray) -> dict:
        """Run the pipeline on a frame and return serialized results."""
        result = self.process_frame_raw(inspection_id, frame)
        return result.to_json()

    def process_frame_raw(self, inspection_id: int, frame: np.ndarray):
        """Run the pipeline on a frame, persist sampled data, trigger alarms.

        Persistence and alarms only run on frames that were actually analyzed
        (the adaptive controller may re-use cached results on stride-skipped
        frames — persisting those would create duplicate rows).
        """
        inspection = self.inspections.get_or_raise(inspection_id)
        result = self.pipeline.process(frame)

        if result.analyzed:
            persist_detail = inspection.frames_processed % DETECTION_SAMPLE_EVERY == 0
            self._persist_detections(inspection, result.detections, persist_detail=persist_detail)
            self._persist_faults(inspection, result.faults, persist_detail=persist_detail)
            result.alarm_events = self._raise_alarms(inspection, result.faults, result)
            self._capture_evidence(inspection, frame, result)

        inspection.frames_processed += 1
        inspection.avg_fps = result.fps if inspection.frames_processed <= 1 else (inspection.avg_fps + result.fps) / 2
        inspection.component_count = result.component_count
        inspection.risk_score = max(inspection.risk_score, result.risk_score)
        self.db.commit()
        return result

    # ------------------------------------------------------------------
    def stop_inspection(self, inspection_id: int, notes: str | None = None) -> Inspection:
        """Complete an inspection (idempotent).

        Only ``running`` inspections transition to ``completed``. A second stop
        (double-click, WS + REST race, reconnect) is a no-op that never resets
        ``ended_at`` and never re-runs incident/maintenance side effects.
        """
        inspection = self.inspections.get_or_raise(inspection_id)
        if inspection.status != "running":
            return inspection
        self.inspections.update(
            inspection,
            status="completed",
            ended_at=datetime.now(UTC),
            notes=notes or inspection.notes,
        )
        self.db.commit()
        logger.info("Inspection %s completed (id=%s)", inspection.inspection_code, inspection.id)
        self._auto_incidents_and_maintenance(inspection)
        self._refresh_device_status(inspection)
        return inspection

    def _refresh_device_status(self, inspection: Inspection) -> None:
        """Recompute the device's derived operational status from new evidence."""
        if inspection.device_id is None:
            return
        try:
            from backend.models.device import Device as DeviceModel
            from backend.services.device_analytics import DeviceAnalyticsService

            device = self.db.get(DeviceModel, inspection.device_id)
            if device is not None:
                DeviceAnalyticsService(self.db).refresh_derived_status(device)
                self.db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to refresh device status after inspection")

    def abort_inspection(self, inspection_id: int) -> Inspection:
        """Abort a running inspection (idempotent).

        Completed inspections are never silently re-marked aborted (e.g. a WS
        disconnect arriving after a graceful stop).
        """
        inspection = self.inspections.get_or_raise(inspection_id)
        if inspection.status != "running":
            return inspection
        self.inspections.update(inspection, status="aborted", ended_at=datetime.now(UTC))
        self.db.commit()
        logger.info("Inspection %s aborted (id=%s)", inspection.inspection_code, inspection.id)
        return inspection

    # ------------------------------------------------------------------
    def _auto_incidents_and_maintenance(self, inspection: Inspection) -> None:
        """Create incident reports + maintenance work orders for critical faults.

        Runs once per inspection (idempotent) when an inspection completes.
        """
        if inspection.status != "completed":
            return
        existing = self.db.scalar(
            select(func.count()).select_from(Incident).where(Incident.inspection_id == inspection.id)
        )
        if existing:
            return

        critical = [f for f in inspection.faults if f.severity == HealthStatus.CRITICAL.value]
        seen: set[tuple[str, str]] = set()
        unique: list[FaultModel] = []
        for fault in critical:
            key = (fault.fault_type, fault.component_label or "")
            if key in seen:
                continue
            seen.add(key)
            unique.append(fault)
        if not unique:
            return

        # Reuse the most recent alarm evidence frame as the incident screenshot
        evidence = None
        if inspection.alarms:
            media = [a.media_path for a in inspection.alarms if a.media_path]
            evidence = media[-1] if media else None
        inspector = inspection.user.username if inspection.user else None
        now = datetime.now(UTC)
        mt_seq = int(self.db.scalar(select(func.count()).select_from(MaintenanceRecord)) or 0)

        for i, fault in enumerate(unique[:5], start=1):
            incident = Incident(
                code=f"INC-{inspection.started_at:%Y%m%d}-{inspection.id}-{i:02d}",
                inspection_id=inspection.id,
                device_id=inspection.device_id,
                panel_id=inspection.panel_id,
                component_id=fault.component_id,
                status="OPEN",
                fault_type=fault.fault_type,
                severity=fault.severity,
                component_label=fault.component_label,
                temperature=fault.temperature,
                occurred_at=now,
                suggested_action=fault.recommendation,
                screenshot_path=evidence,
                annotated_path=evidence,
                evidence_thermal_path=inspection.thermal_image_path,
                inspector=inspector,
            )
            self.db.add(incident)
            self.db.flush()
            mt_seq += 1
            self.db.add(
                MaintenanceRecord(
                    code=f"MT-{mt_seq:05d}",
                    inspection_id=inspection.id,
                    incident_id=incident.id,
                    component_id=self._resolve_component_id(inspection.panel_id, fault.component_label),
                    component_label=fault.component_label,
                    fault_type=fault.fault_type,
                    priority="critical",
                    status="pending",
                    deadline=date.today() + timedelta(days=MAINTENANCE_DEADLINE_DAYS),
                )
            )
            logger.info(
                "Auto-created incident %s + maintenance MT-%05d for inspection %s (%s)",
                incident.code,
                mt_seq,
                inspection.inspection_code,
                fault.fault_type,
            )
        self.db.commit()

    # ------------------------------------------------------------------
    def _persist_detections(self, inspection: Inspection, detections, persist_detail: bool) -> None:  # noqa: ANN001
        for det in detections:
            component = self._match_component(inspection.panel_id, det.label, det)
            if persist_detail:
                self.db.add(
                    Detection(
                        inspection_id=inspection.id,
                        component_id=component.id if component else None,
                        label=det.label,
                        confidence=det.confidence,
                        bbox=json.dumps(list(det.bbox)),
                        temperature=det.temperature,
                        health=det.health.value,
                        frame_index=inspection.frames_processed,
                    )
                )
            # temperature history is always recorded (cheap, high value for trends) —
            # even without a mapped panel/component, so post-inspection graphs work.
            if det.temperature is not None:
                self.db.add(
                    TemperatureReading(
                        component_id=component.id if component else None,
                        component_label=component.label if component else det.label,
                        inspection_id=inspection.id,
                        temperature=det.temperature,
                        reading_time=datetime.now(UTC),
                        source="thermal",
                    )
                )

    def _persist_faults(self, inspection: Inspection, faults: list[Fault], persist_detail: bool = True) -> None:
        """Persist non-healthy faults, deduplicated per inspection.

        Faults are written on the same sampling cadence as detection details
        (every DETECTION_SAMPLE_EVERY analyzed frames) and are upserted by
        (fault_type, component_label): a repeated fault updates its row instead
        of appending thousands of duplicate rows per session — this keeps the
        fault history bounded while remaining complete and honest.
        """
        if not persist_detail or not faults:
            return
        to_write = [
            f
            for f in faults
            if f.severity != HealthStatus.HEALTHY and f.fault_type != "heat_verifying"  # transient — never persisted
        ]
        if not to_write:
            return

        existing: dict[tuple[str, str | None], FaultModel] = {
            (row.fault_type, row.component_label): row
            for row in self.db.scalars(select(FaultModel).where(FaultModel.inspection_id == inspection.id)).all()
        }
        for f in to_write:
            row = existing.get((f.fault_type, f.component_label))
            if row is not None:
                # Repeated fault → keep the row, refresh with the latest reading.
                row.severity = f.severity.value
                row.confidence = f.confidence
                row.temperature = f.temperature
                row.message = f.message
                row.recommendation = f.recommendation
            else:
                row = FaultModel(
                    inspection_id=inspection.id,
                    component_id=self._resolve_component_id(inspection.panel_id, f.component_label),
                    component_label=f.component_label,
                    fault_type=f.fault_type,
                    severity=f.severity.value,
                    confidence=f.confidence,
                    temperature=f.temperature,
                    message=f.message,
                    recommendation=f.recommendation,
                )
                self.db.add(row)
                existing[(f.fault_type, f.component_label)] = row

    def _capture_evidence(self, inspection: Inspection, frame: np.ndarray, result) -> None:  # noqa: ANN001
        """Persist original / annotated / thermal screenshots as PDF evidence.

        Captured whenever a critical/high (overload) fault is present, and
        periodically otherwise, so generated reports always embed real captures
        taken during the inspection — never placeholders.
        """
        has_overload = any(f.severity in (HealthStatus.CRITICAL, HealthStatus.HIGH_RISK) for f in result.faults)
        if not has_overload and inspection.frames_processed % EVIDENCE_EVERY != 0:
            return
        try:
            import cv2

            frames_dir = self.settings.media_path / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            base = f"insp{inspection.id}_{int(time.time())}"
            original = frames_dir / f"{base}_original.jpg"
            annotated = frames_dir / f"{base}_annotated.jpg"
            thermal = frames_dir / f"{base}_thermal.jpg"

            for path, img in ((original, frame), (annotated, result.frame)):
                ok, buf = cv2.imencode(".jpg", img)
                if ok:
                    path.write_bytes(buf.tobytes())

            # Heat map reuses the last analysis — no second full pipeline run.
            overlay = self.pipeline.thermal_overlay_for(frame)
            if overlay is not None:
                ok, buf = cv2.imencode(".jpg", overlay)
                if ok:
                    thermal.write_bytes(buf.tobytes())
                    thermal_path = str(thermal)
                else:
                    thermal_path = None
            else:
                thermal_path = None

            self.inspections.update(
                inspection,
                original_image_path=str(original),
                annotated_image_path=str(annotated),
                thermal_image_path=thermal_path,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to capture inspection evidence screenshots")

    def _resolve_component_id(self, panel_id: int | None, label: str | None) -> int | None:
        """Find an existing component on a panel by label (no auto-create)."""
        if panel_id is None or not label:
            return None
        component = self.db.scalar(
            select(Component).where(Component.panel_id == panel_id, Component.label == label)
        )
        return component.id if component else None

    def _raise_alarms(self, inspection: Inspection, faults: list[Fault], result) -> list[dict]:  # noqa: ANN001
        """Raise alarms for critical/high faults with per-fault cooldown.

        Returns the list of alarm events actually raised (cooldown-filtered)
        so the transport layer can notify clients without re-spamming.
        """
        critical = [
            f
            for f in faults
            if f.severity in (HealthStatus.CRITICAL, HealthStatus.HIGH_RISK)
            and f.fault_type != "heat_verifying"  # not confirmed yet — never alarms
        ]
        if not critical:
            return []
        now = time.time()
        pending = []
        for f in critical:
            key = f"{f.fault_type}|{f.component_label or 'panel'}"
            if now - self._last_alarm.get(key, 0.0) < ALARM_COOLDOWN_S:
                continue
            self._last_alarm[key] = now
            pending.append(f)
        if not pending:
            return []

        # save annotated frame as evidence (once per batch)
        media_path = None
        try:
            fname = f"alarm_insp{inspection.id}_{int(time.time())}.jpg"
            path = self.settings.media_path / "frames" / fname
            ok, buf = __import__("cv2").imencode(".jpg", result.frame)
            if ok:
                path.write_bytes(buf.tobytes())
                media_path = str(path)
        except Exception:
            logger.exception("Failed to save alarm evidence frame")

        raised: list[dict] = []
        for f in pending[:3]:
            message = f.message or f"{f.fault_type} detected on {f.component_label or 'panel'}"
            self.alarm_service.raise_alarm(
                message=message,
                severity=f.severity.value,
                inspection_id=inspection.id,
                source="ai-pipeline",
                media_path=media_path,
                payload={"fault_type": f.fault_type, "temperature": f.temperature, "confidence": f.confidence},
                user_id=inspection.user_id,
                sound=f.fault_type == "overloaded_circuit",  # buzzer ONLY for confirmed circuit heat
            )
            raised.append({"severity": f.severity.value, "message": message, "recommendation": f.recommendation})
        return raised

    def _match_component(self, panel_id: int | None, label: str, det) -> Component | None:  # noqa: ANN001
        if panel_id is None:
            return None
        component = self.components.upsert_by_label(panel_id, label, det.label)
        return component

    def get(self, inspection_id: int) -> Inspection:
        inspection = self.inspections.get_with_details(inspection_id)
        if inspection is None:
            raise NotFoundError(f"Inspection {inspection_id} not found")
        return inspection
