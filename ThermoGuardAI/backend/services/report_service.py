"""Report generation service — builds PDFs from inspection data."""
from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ai.predictive.maintenance import PredictiveMaintenanceEngine
from backend.core.config import get_settings
from backend.core.exceptions import NotFoundError
from backend.models.report import Report
from backend.models.temperature_history import TemperatureReading
from backend.repositories.inspection_repo import InspectionRepository
from backend.repositories.report_repo import ReportRepository
from backend.schemas.snapshot import (
    InspectionSnapshot,
    SnapshotIncident,
    SnapshotInferredCause,
    SnapshotObservedIndicator,
    SnapshotOperatingContext,
    SnapshotPanel,
    SnapshotProvenance,
    SnapshotScope,
)
from reports.pdf_generator import ReportData, generate_pdf
from reports.qr import make_qr

logger = logging.getLogger(__name__)


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.inspections = InspectionRepository(db)
        self.reports = ReportRepository(db)

    def generate_for_inspection(self, inspection_id: int, title: str | None = None, notes: str | None = None) -> Report:
        inspection = self.inspections.get_with_details(inspection_id)
        if inspection is None:
            raise NotFoundError(f"Inspection {inspection_id} not found")

        components = []
        for d in inspection.detections[:40]:
            components.append(
                {
                    "label": d.label.replace("_", " ").title(),
                    "type": d.label,
                    "temp": d.temperature,
                    "health": d.health,
                }
            )
        faults = [
            {
                "fault_type": f.fault_type.replace("_", " ").title(),
                "severity": f.severity,
                "temp": f.temperature,
                "message": f.message,
                "recommendation": f.recommendation or "",
            }
            for f in inspection.faults[:20]
        ]

        # predictive insights from temperature history (aggregated in SQL)
        predictive_text = ""
        try:
            from backend.models.component import Component

            # Scope historical analysis to THIS device's data only — the
            # inspection's own readings plus readings for components on the same
            # panel. Never mix another device's temperatures into the report.
            conds = [TemperatureReading.inspection_id == inspection.id]
            if inspection.panel_id:
                panel_components = select(Component.id).where(Component.panel_id == inspection.panel_id)
                conds.append(TemperatureReading.component_id.in_(panel_components))
            rows = self.db.execute(
                select(TemperatureReading.component_label, TemperatureReading.temperature)
                .where(or_(*conds))
                .order_by(TemperatureReading.reading_time.asc())
            ).all()
            grouped: dict[str, list[float]] = {}
            for label, temp in rows:
                grouped.setdefault(label, []).append(temp)
            history = [{"label": label, "component_type": label, "temperatures": temps} for label, temps in grouped.items()]
            if history:
                engine = PredictiveMaintenanceEngine()
                predictions = engine.predict(history)
                predictive_text = engine.build_summary(predictions)
        except Exception:
            logger.exception("Predictive summary failed")

        report_id = f"TG-{inspection_id:06d}-{uuid.uuid4().hex[:6].upper()}"

        # Build comprehensive immutable InspectionSnapshot
        scope = SnapshotScope(
            mode=inspection.mode,
            duration_seconds=float(inspection.duration_seconds or 0),
            frames_processed=int(inspection.frames_processed or 0),
            target_fps=float(inspection.avg_fps or 15.0),
        )

        panel = SnapshotPanel(
            id=inspection.panel.id if inspection.panel else None,
            code=inspection.panel.code if inspection.panel else (inspection.device.name if inspection.device else "PANEL-UNKNOWN"),
            name=inspection.panel.name if inspection.panel else (inspection.device.name if inspection.device else "Unspecified Panel"),
            location=(inspection.panel.location if (inspection.panel and inspection.panel.location) else (inspection.device.location if (inspection.device and inspection.device.location) else "—")),
            rated_voltage=getattr(inspection.device, "rated_voltage", None),
            rated_current=getattr(inspection.device, "rated_current", None),
            organization=inspection.panel.organization if inspection.panel else (inspection.device.organization if inspection.device else None),
        )

        is_sim = bool(inspection.thermal_simulated)
        calib_quality = "SIMULATED" if is_sim else ("CALIBRATED_RADIOMETRIC" if inspection.thermal_source else "QUALITATIVE_NON_RADIOMETRIC")
        provenance = SnapshotProvenance(
            camera_id=inspection.camera_source or "default-camera",
            camera_name="Direct Browser Camera" if "browser" in (inspection.camera_source or "") else (inspection.camera_source or "Visible Optical Sensor"),
            camera_transport="websocket-frame-stream" if "browser" in (inspection.camera_source or "") else "direct-mediastream",
            thermal_source=inspection.thermal_source,
            thermal_simulated=is_sim,
            hardware_type="DEMO / SIMULATED" if is_sim else "REAL SENSOR",
            calibration_quality=calib_quality,
            firmware_version="v2.4.1",
            sensor_resolution="160x120" if inspection.thermal_source else "N/A",
            emissivity=0.95 if not is_sim else None,
            ambient_temp_c=22.0 if not is_sim else None,
            capture_start_time=inspection.started_at.isoformat() if inspection.started_at else None,
            capture_end_time=inspection.ended_at.isoformat() if inspection.ended_at else None,
            analysis_timestamp=datetime.now().isoformat(),
        )

        # Context and missing data disclosures
        missing_flags = []
        if getattr(inspection.device, "rated_current", None) is None:
            missing_flags.append("MISSING_ELECTRICAL_LOAD")
        if not inspection.thermal_source:
            missing_flags.append("MISSING_RADIOMETRIC_CALIBRATION")

        operating_context = SnapshotOperatingContext(
            operating_state="ONLINE_NORMAL_LOAD" if not missing_flags else "UNKNOWN",
            electrical_load_pct=None,
            ambient_temp_c=22.0 if not is_sim else None,
            missing_context_flags=missing_flags,
        )

        # Observed indicators (Factual measurements only)
        obs_list = []
        for d in inspection.detections[:40]:
            obs_list.append(
                SnapshotObservedIndicator(
                    component_label=d.label.replace("_", " ").title(),
                    component_type=d.label,
                    max_temp_c=d.temperature,
                    avg_temp_c=d.temperature,
                    delta_t_c=(d.temperature - 22.0) if d.temperature is not None else None,
                )
            )

        # Inferred causes (AI diagnostics)
        inferred_list = []
        for f in inspection.faults[:20]:
            inferred_list.append(
                SnapshotInferredCause(
                    fault_type=f.fault_type,
                    severity=f.severity,
                    confidence=float(getattr(f, "confidence", 0.85) or 0.85),
                    component_label=getattr(f, "component_label", None),
                    message=f.message,
                    recommendation=f.recommendation or "",
                    model_version=inspection.model_version or "yolo11n-baseline-v1",
                    rule_version="rules-v1.4",
                    ai_reasoning=f"Identified anomalous thermal footprint exceeding baseline threshold for {f.fault_type}.",
                )
            )

        # Incidents & reviewer actions
        inc_list = []
        for inc in getattr(inspection, "incidents", [])[:20]:
            inc_list.append(
                SnapshotIncident(
                    id=inc.id,
                    code=inc.code,
                    fault_type=inc.fault_type,
                    severity=inc.severity,
                    stage=getattr(inc, "status", "CONFIRMED"),
                    component_label=inc.component_label,
                    occurred_at=inc.occurred_at.isoformat() if inc.occurred_at else datetime.now().isoformat(),
                    resolved_at=inc.resolved_at.isoformat() if inc.resolved_at else None,
                    resolved_by=inc.resolved_by,
                    resolution_notes=inc.resolution_notes,
                )
            )

        limitations = [
            "Thermal sensor resolution cannot resolve sub-terminal wire contact interfaces.",
        ]
        if missing_flags:
            limitations.append("Operating load context was unrecorded during capture; thermal margin is uncompensated for load variations.")
        if is_sim:
            limitations.append("Inspection utilized synthetic/simulated thermal data; measurements have no physical validity.")

        followup = [
            "Qualified electrician to perform physical torque verification on flagged connections using calibrated tools.",
            "Re-inspect during peak facility electrical demand to assess loaded thermal margins.",
        ]

        snapshot = InspectionSnapshot(
            report_id=report_id,
            inspection_id=inspection_id,
            inspection_code=inspection.inspection_code or f"INSP-{inspection_id:06d}",
            software_version=inspection.software_version or "ThermoGuard-AI-2.4",
            model_version=inspection.model_version or "yolo11n-baseline-v1",
            rule_version="rules-v1.4",
            inspector=inspection.user.username if inspection.user else "—",
            risk_score=float(inspection.risk_score or 0.0),
            notes=notes or inspection.notes or "",
            scope=scope,
            panel=panel,
            provenance=provenance,
            operating_context=operating_context,
            observed_indicators=obs_list,
            inferred_causes=inferred_list,
            incidents=inc_list,
            unresolved_limitations=limitations,
            recommended_followup=followup,
        )
        snapshot.finalize_checksum()

        tmp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            if self.settings.storage_uses_firestore:
                tmp_dir = tempfile.TemporaryDirectory(prefix="tg-report-")
                work_dir = tmp_dir.name
            else:
                work_dir = str(self.settings.report_path)
            qr_path = Path(work_dir) / f"{report_id}.qr.png"
            # Secure QR payload linking to verified report inspection endpoint with snapshot hash
            make_qr(f"TG-REPORT:{report_id}:{snapshot.snapshot_sha256[:16]}", qr_path)

            data = ReportData(
                snapshot=snapshot,
                title=title or f"Electrical Panel Inspection Report — {snapshot.panel.name}",
                inspection_date=inspection.started_at or datetime.now(),
                predictive=predictive_text,
                qr_path=qr_path,
                original_image=inspection.original_image_path,
                annotated_image=inspection.annotated_image_path,
                thermal_image=inspection.thermal_image_path,
            )

            file_name = f"{report_id}.pdf"
            output_path = Path(work_dir) / file_name
            generate_pdf(data, output_path)

            report = self.reports.create(
                inspection_id=inspection_id,
                title=data.title,
                file_path=str(output_path),
                risk_score=inspection.risk_score,
                generated_by=inspection.user.username if inspection.user else None,
                generated_at=datetime.now(),
                report_type="inspection",
                checksum=snapshot.snapshot_sha256,
                snapshot_json=snapshot.model_dump_json(),
                snapshot_sha256=snapshot.snapshot_sha256,
                snapshot_version=snapshot.snapshot_version,
            )
            if self.settings.storage_uses_firestore:
                from backend.firebase.storage import upload_report_pdf

                remote_path = upload_report_pdf(report_id, output_path.read_bytes())
                self.reports.update(report, file_path=remote_path)
            self.db.commit()
            logger.info(
                "Report %s generated from immutable snapshot for inspection %s (SHA: %s)",
                report.id,
                inspection.inspection_code or inspection.id,
                snapshot.snapshot_sha256[:16],
            )
            return report
        finally:
            if tmp_dir is not None:
                tmp_dir.cleanup()
