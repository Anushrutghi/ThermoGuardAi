"""Professional PDF inspection report generator (ReportLab).

Generates verifiable, immutable inspection reports conforming to industrial
traceability and safety-critical documentation standards.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.schemas.snapshot import (
    InspectionSnapshot,
    SnapshotComponent,
    SnapshotIncident,
    SnapshotInferredCause,
    SnapshotObservedIndicator,
    SnapshotOperatingContext,
    SnapshotPanel,
    SnapshotProvenance,
    SnapshotScope,
    compute_canonical_sha256,
)

logger = logging.getLogger(__name__)

ACCENT = colors.HexColor("#0E4DA4")
ACCENT_LIGHT = colors.HexColor("#EEF2F8")
WARN = colors.HexColor("#E8A33D")
WARN_LIGHT = colors.HexColor("#FFF7EB")
DANGER = colors.HexColor("#D64545")
DANGER_LIGHT = colors.HexColor("#FDF2F2")
GOOD = colors.HexColor("#2E8B57")
GOOD_LIGHT = colors.HexColor("#EDF7F1")
TEXT_MUTED = colors.HexColor("#64748B")
BORDER_LIGHT = colors.HexColor("#CBD5E1")


def _register_fonts() -> None:
    """Register a Unicode-capable font so component labels render safely."""
    import os

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", path))
                return
            except Exception:
                continue
    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    registerFontFamily("AppFont", normal="Helvetica", bold="Helvetica-Bold", italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique")


_register_fonts()


def _style() -> dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Title"], fontName="AppFont", fontSize=18, textColor=ACCENT, spaceAfter=2, alignment=TA_LEFT),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="AppFont", fontSize=11.5, textColor=ACCENT, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName="AppFont", fontSize=8.5, leading=11.5),
        "body_bold": ParagraphStyle("body_bold", parent=ss["BodyText"], fontName="AppFont", fontSize=8.5, leading=11.5, fontStyle="bold"),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontName="AppFont", fontSize=7.5, leading=9.5, textColor=TEXT_MUTED),
        "center": ParagraphStyle("center", parent=ss["BodyText"], fontName="AppFont", fontSize=8.5, leading=11, alignment=TA_CENTER),
        "right": ParagraphStyle("right", parent=ss["BodyText"], fontName="AppFont", fontSize=8, leading=10, alignment=TA_RIGHT, textColor=TEXT_MUTED),
        "mono": ParagraphStyle("mono", parent=ss["BodyText"], fontName="Courier", fontSize=7.5, leading=9.5),
        "disclaimer": ParagraphStyle("disclaimer", parent=ss["BodyText"], fontName="AppFont", fontSize=7, leading=9, textColor=colors.HexColor("#475569"), alignment=TA_JUSTIFY),
    }


class ReportData:
    """Consolidated report data container backed by an immutable InspectionSnapshot."""

    def __init__(self, snapshot: InspectionSnapshot | None = None, **kwargs: Any) -> None:
        if snapshot is not None:
            self.snapshot = snapshot
        else:
            # Build an InspectionSnapshot from legacy or direct kwargs for backward compatibility
            scope = SnapshotScope(
                mode=str(kwargs.get("mode", "manual")),
                duration_seconds=float(kwargs.get("duration_s", 0)),
                frames_processed=int(kwargs.get("frames", 0)),
            )
            panel = SnapshotPanel(
                name=str(kwargs.get("panel_name", "—")),
                location=str(kwargs.get("location", "—")),
            )
            provenance = SnapshotProvenance(
                thermal_source=kwargs.get("thermal_source"),
                thermal_simulated=bool(kwargs.get("thermal_simulated", False)),
                hardware_type="DEMO / SIMULATED" if kwargs.get("thermal_simulated") else "REAL SENSOR",
                calibration_quality="SIMULATED" if kwargs.get("thermal_simulated") else "CALIBRATED_RADIOMETRIC",
            )
            obs = []
            for c in kwargs.get("components", []):
                obs.append(
                    SnapshotObservedIndicator(
                        component_label=str(c.get("label", "—")),
                        component_type=str(c.get("type", "component")),
                        max_temp_c=c.get("temp"),
                    )
                )
            inferred = []
            for f in kwargs.get("faults", []):
                inferred.append(
                    SnapshotInferredCause(
                        fault_type=str(f.get("fault_type", "—")),
                        severity=str(f.get("severity", "—")),
                        message=str(f.get("message", "")),
                        recommendation=str(f.get("recommendation", "")),
                    )
                )
            snap = InspectionSnapshot(
                report_id=str(kwargs.get("report_id", "TG-000000")),
                inspection_id=int(kwargs.get("inspection_id", 0)),
                inspection_code=str(kwargs.get("inspection_code", f"INSP-{kwargs.get('report_id', '000000')}")),
                inspector=str(kwargs.get("inspector", "—")),
                risk_score=float(kwargs.get("risk_score", 0.0)),
                notes=str(kwargs.get("notes", "")),
                scope=scope,
                panel=panel,
                provenance=provenance,
                observed_indicators=obs,
                inferred_causes=inferred,
            )
            snap.finalize_checksum()
            self.snapshot = snap

        # Properties exposed directly for convenient access
        self.title = str(kwargs.get("title", f"Electrical Inspection Report — {self.snapshot.panel.name}"))
        self.report_id = self.snapshot.report_id
        self.inspector = self.snapshot.inspector
        self.inspection_date = kwargs.get("inspection_date", self.snapshot.snapshot_timestamp)
        self.location = self.snapshot.panel.location
        self.panel_name = self.snapshot.panel.name
        self.mode = self.snapshot.scope.mode
        self.duration_s = self.snapshot.scope.duration_seconds
        self.frames = self.snapshot.scope.frames_processed
        self.risk_score = self.snapshot.risk_score
        self.component_count = len(self.snapshot.components) or len(self.snapshot.observed_indicators)
        self.notes = self.snapshot.notes
        self.original_image = kwargs.get("original_image")
        self.thermal_image = kwargs.get("thermal_image")
        self.annotated_image = kwargs.get("annotated_image")
        self.predictive = kwargs.get("predictive", "")
        self.qr_path = kwargs.get("qr_path")

        # Provenance & Honesty flags
        self.thermal_source = self.snapshot.provenance.thermal_source
        self.thermal_simulated = self.snapshot.provenance.thermal_simulated
        self.calibration_quality = self.snapshot.provenance.calibration_quality
        self.snapshot_sha256 = self.snapshot.snapshot_sha256

        # Formatted lists for rendering
        self.components = [c.model_dump() for c in self.snapshot.components]
        self.observed_indicators = self.snapshot.observed_indicators
        self.inferred_causes = self.snapshot.inferred_causes
        self.incidents = self.snapshot.incidents
        self.unresolved_limitations = self.snapshot.unresolved_limitations
        self.recommended_followup = self.snapshot.recommended_followup
        self.operating_context = self.snapshot.operating_context


def build_report_story(data: ReportData) -> list[Any]:
    """Build the Platypus flowable story for the immutable PDF report."""
    s = _style()
    story: list[Any] = []

    # =========================================================================
    # 1. Header & Verifiable Metadata Fingerprint
    # =========================================================================
    fingerprint = data.snapshot_sha256[:20] if data.snapshot_sha256 else "UNVERIFIED"
    header_table = Table(
        [
            [
                Paragraph("<b>ThermoGuard AI</b> · Electrical Safety & Inspection", s["small"]),
                Paragraph(f"Immutable Snapshot SHA-256: <code>{fingerprint}…</code>", s["right"]),
            ]
        ],
        colWidths=[90 * mm, 92 * mm],
    )
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT, spaceBefore=2, spaceAfter=6))

    story.append(Paragraph(data.title, s["h1"]))
    story.append(Paragraph(f"Report ID: <b>{data.report_id}</b> &nbsp;•&nbsp; Generated: {data.inspection_date}", s["small"]))
    story.append(Spacer(1, 4))

    # =========================================================================
    # 2. Scope & Asset Identification Table
    # =========================================================================
    meta = [
        [
            Paragraph("<b>Panel Name:</b>", s["body"]), Paragraph(data.panel_name, s["body"]),
            Paragraph("<b>Panel Code:</b>", s["body"]), Paragraph(data.snapshot.panel.code, s["body"]),
        ],
        [
            Paragraph("<b>Location:</b>", s["body"]), Paragraph(data.location, s["body"]),
            Paragraph("<b>Inspector:</b>", s["body"]), Paragraph(data.inspector, s["body"]),
        ],
        [
            Paragraph("<b>Inspection Mode:</b>", s["body"]), Paragraph(data.mode, s["body"]),
            Paragraph("<b>Duration / Frames:</b>", s["body"]), Paragraph(f"{data.duration_s:.1f} s ({data.frames} frames)", s["body"]),
        ],
        [
            Paragraph("<b>Software Version:</b>", s["body"]), Paragraph(data.snapshot.software_version, s["body"]),
            Paragraph("<b>Model Version:</b>", s["body"]), Paragraph(data.snapshot.model_version, s["body"]),
        ],
    ]
    meta_table = Table(meta, colWidths=[30 * mm, 61 * mm, 30 * mm, 61 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), ACCENT_LIGHT),
                ("BACKGROUND", (2, 0), (2, -1), ACCENT_LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6))

    # =========================================================================
    # 3. Source Provenance, Calibration Quality & Operating Context
    # =========================================================================
    prov = data.snapshot.provenance
    calib_label = prov.calibration_quality.replace("_", " ").title()
    emiss_label = f"{prov.emissivity:.2f}" if prov.emissivity is not None else "Unspecified"
    prov_rows = [
        [
            Paragraph("<b>Optical Sensor:</b>", s["body"]), Paragraph(f"{prov.camera_name} ({prov.camera_transport})", s["body"]),
            Paragraph("<b>Thermal Sensor:</b>", s["body"]), Paragraph(f"{prov.thermal_source or 'None'} (Emissivity: {emiss_label})", s["body"]),
        ],
        [
            Paragraph("<b>Calibration:</b>", s["body"]), Paragraph(f"<b>{calib_label}</b>", s["body"]),
            Paragraph("<b>Sensor Mode:</b>", s["body"]), Paragraph(prov.hardware_type, s["body"]),
        ],
    ]
    prov_table = Table(prov_rows, colWidths=[30 * mm, 61 * mm, 30 * mm, 61 * mm])
    prov_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER_LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(prov_table)
    story.append(Spacer(1, 6))

    # Operating context & missing data disclosures
    ctx = data.operating_context
    if ctx.missing_context_flags:
        flags_text = ", ".join(f.replace("_", " ").title() for f in ctx.missing_context_flags)
        warn_box = Table(
            [[
                Paragraph(
                    f"<b>MISSING CONTEXT DISCLOSURE:</b> {flags_text}. "
                    "Thermal readings could not be normalized for operational electrical loading or ambient drift.",
                    ParagraphStyle("warn_ctx", parent=s["body"], textColor=colors.HexColor("#78350F"), fontSize=8),
                )
            ]],
            colWidths=[182 * mm],
        )
        warn_box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), WARN_LIGHT),
                    ("BOX", (0, 0), (-1, -1), 1, WARN),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(warn_box)
        story.append(Spacer(1, 6))

    # Overall Risk Score Banner
    risk_color = DANGER if data.risk_score >= 60 else WARN if data.risk_score >= 25 else GOOD
    story.append(
        Table(
            [[Paragraph(f"Assessed Operational Risk Score: <b>{data.risk_score:.0f} / 100</b>", ParagraphStyle("r", parent=s["body"], textColor=colors.white, fontSize=10.5, alignment=TA_CENTER))]],
            colWidths=[182 * mm],
        )
    )
    story[-1].setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), risk_color), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))

    # DEMO / SIMULATED Warning Banner
    if data.thermal_simulated or prov.hardware_type == "DEMO / SIMULATED":
        sim_banner = Table(
            [[
                Paragraph(
                    "<b>⚠ SIMULATED THERMAL DATA NOTICE:</b> No radiometric physical thermal sensor was connected. "
                    "All temperatures and thermal profiles in this document are synthetic test fixtures and MUST NOT "
                    "be used for operational or safety decisions.",
                    ParagraphStyle("sim_notice", parent=s["body"], textColor=colors.white, fontSize=8.5, leading=11, alignment=TA_CENTER),
                )
            ]],
            colWidths=[182 * mm],
        )
        sim_banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#7F1D1D")), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.append(Spacer(1, 4))
        story.append(sim_banner)

    story.append(Spacer(1, 6))

    # =========================================================================
    # 4. Capture Evidence (Images)
    # =========================================================================
    story.append(Paragraph("Visual & Thermal Capture Evidence", s["h2"]))
    images_rendered = 0
    for label, path in (("Visible Optical Feed", data.original_image), ("AI-Annotated Tracking Overlay", data.annotated_image), ("Radiometric / Thermal Image", data.thermal_image)):
        if path and Path(path).exists():
            story.append(Paragraph(f"<b>{label}</b>", s["small"]))
            story.append(Image(str(path), width=180 * mm, height=90 * mm, kind="proportional"))
            story.append(Spacer(1, 4))
            images_rendered += 1

    if images_rendered == 0:
        story.append(Paragraph("<i>No visual or thermal image frames were archived for this snapshot.</i>", s["small"]))

    story.append(Spacer(1, 6))

    # =========================================================================
    # 5. Table 1: Observed Physical Indicators (Factual Measurements Only)
    # =========================================================================
    story.append(Paragraph("1. Observed Physical Indicators (Factual Measurements)", s["h2"]))
    story.append(Paragraph("Direct sensor observations prior to diagnostic inference or algorithmic interpretation.", s["small"]))
    story.append(Spacer(1, 2))

    obs_rows = [["#", "Component", "Type", "Spot Tmax", "Avg Temp", "ΔT (Ambient)", "Visual Condition"]]
    indicators = data.observed_indicators or []
    if indicators:
        for i, obs in enumerate(indicators, start=1):
            tmax = f"{obs.max_temp_c:.1f} °C" if obs.max_temp_c is not None else "—"
            tavg = f"{obs.avg_temp_c:.1f} °C" if obs.avg_temp_c is not None else "—"
            delta = f"+{obs.delta_t_c:.1f} °C" if obs.delta_t_c is not None else "—"
            vis = "Discolored" if (obs.visual_discoloration_score or 0) > 0.4 else "Normal Appearance"
            obs_rows.append([str(i), obs.component_label, obs.component_type, tmax, tavg, delta, vis])
    else:
        obs_rows.append(["—", "No components registered", "—", "—", "—", "—", "—"])

    obs_table = Table(obs_rows, colWidths=[8 * mm, 46 * mm, 32 * mm, 24 * mm, 24 * mm, 24 * mm, 24 * mm])
    obs_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "AppFont"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER_LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ACCENT_LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(obs_table)
    story.append(Spacer(1, 8))

    # =========================================================================
    # 6. Table 2: Inferred Diagnostic Hypotheses & Causes
    # =========================================================================
    story.append(Paragraph("2. Inferred Diagnostic Hypotheses (AI Diagnostic Interpretation)", s["h2"]))
    story.append(Paragraph("Algorithmic fault hypotheses derived from thermal gradients, peer deviations, and visual evidence.", s["small"]))
    story.append(Spacer(1, 2))

    inferred = data.inferred_causes or []
    if inferred:
        inf_rows = [["#", "Inferred Fault", "Severity", "Confidence", "Model / Rule Version", "Diagnostic Description"]]
        for i, inf in enumerate(inferred, start=1):
            conf_str = f"{inf.confidence * 100:.1f}%" if inf.confidence > 0 else "—"
            ver_str = f"{inf.model_version} / {inf.rule_version}"
            desc = inf.message or "Anomalous thermal signature detected."
            inf_rows.append([str(i), inf.fault_type.replace("_", " ").title(), inf.severity.upper(), conf_str, ver_str, desc])

        inf_table = Table(inf_rows, colWidths=[8 * mm, 38 * mm, 22 * mm, 18 * mm, 38 * mm, 58 * mm])
        inf_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "AppFont"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER_LIGHT),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(inf_table)
    else:
        story.append(Paragraph("<i>No diagnostic faults or anomalies inferred from this snapshot.</i>", s["body"]))

    story.append(Spacer(1, 8))

    # =========================================================================
    # 7. Table 3: Incident Lifecycle & Reviewer Actions
    # =========================================================================
    story.append(Paragraph("3. Incident Lifecycle & Reviewer Actions", s["h2"]))
    incidents = data.incidents or []
    if incidents:
        inc_rows = [["Code", "Fault Type", "Stage", "Acknowledged By", "Override Justification / Reviewer Notes"]]
        for inc in incidents:
            ack = inc.acknowledged_by or "Unacknowledged"
            notes = inc.override_reason or inc.resolution_notes or "Standard automated escalation path"
            inc_rows.append([inc.code, inc.fault_type, inc.stage, ack, notes])

        inc_table = Table(inc_rows, colWidths=[30 * mm, 36 * mm, 24 * mm, 32 * mm, 60 * mm])
        inc_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "AppFont"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#475569")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER_LIGHT),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(inc_table)
    else:
        story.append(Paragraph("<i>No persistent incidents tracked for this inspection session.</i>", s["small"]))

    story.append(Spacer(1, 8))

    # =========================================================================
    # 8. Unresolved Limitations & Recommended Qualified Follow-up
    # =========================================================================
    limitations = data.unresolved_limitations or [
        "Thermal sensor resolution cannot resolve sub-terminal wire contact interfaces.",
        "Inspection performed under unmeasured electrical load; load changes will alter thermal signatures.",
    ]
    followup = data.recommended_followup or [
        "Qualified electrician to perform physical torque verification on flagged connections using calibrated tools.",
        "Re-inspect during peak facility electrical demand to assess loaded thermal margins.",
    ]

    keep_box = []
    keep_box.append(Paragraph("4. Unresolved Observational Limitations", s["h2"]))
    for lim in limitations:
        keep_box.append(Paragraph(f"• {lim}", s["body"]))
    keep_box.append(Spacer(1, 4))

    keep_box.append(Paragraph("5. Recommended Qualified Follow-up", s["h2"]))
    for fol in followup:
        keep_box.append(Paragraph(f"• {fol}", s["body"]))
    keep_box.append(Spacer(1, 8))

    # Notes if any
    if data.notes:
        keep_box.append(Paragraph("Operator Inspection Notes", s["h2"]))
        keep_box.append(Paragraph(data.notes, s["body"]))
        keep_box.append(Spacer(1, 6))

    # =========================================================================
    # 9. Regulatory Safety Disclaimer (Non-Certification Mandate)
    # =========================================================================
    keep_box.append(
        Table(
            [[
                Paragraph(
                    "<b>NON-CERTIFICATION NOTICE:</b> This automated inspection report was generated by ThermoGuard AI as an "
                    "inspection-assistance tool. This document does NOT certify electrical equipment safety, operational "
                    "fitness, or compliance with NFPA 70E, IEEE, or OSHA standards. A physical examination by a licensed and "
                    "certified electrical professional is required prior to taking equipment into or out of service.",
                    s["disclaimer"],
                )
            ]],
            colWidths=[182 * mm],
        )
    )
    keep_box[-1].setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_LIGHT),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    keep_box.append(Spacer(1, 8))

    # =========================================================================
    # 10. Manual Reviewer Sign-Off Placeholder & Traceable QR Code
    # =========================================================================
    sign_block = []
    sign_block.append(Paragraph("<b>Inspector Physical Sign-Off (Manual Acknowledgment)</b>", s["body_bold"]))
    sign_block.append(Spacer(1, 3))
    sign_block.append(Paragraph("Signature: ____________________________________ &nbsp;&nbsp;&nbsp;&nbsp; Date: ________________", s["body"]))
    sign_block.append(Spacer(1, 3))
    sign_block.append(Paragraph("Printed Name: ________________________________ &nbsp;&nbsp;&nbsp;&nbsp; License / ID: ____________", s["body"]))
    sign_block.append(Spacer(1, 3))
    sign_block.append(Paragraph("<i>NOTICE: This signature line is a manual physical sign-off placeholder for printed copies and does NOT constitute a cryptographic digital signature.</i>", s["small"]))

    footer_content = [
        Table(
            [
                [
                    Image(str(data.qr_path), width=24 * mm, height=24 * mm) if data.qr_path and Path(data.qr_path).exists() else Paragraph("QR Verified", s["small"]),
                    sign_block,
                ]
            ],
            colWidths=[28 * mm, 154 * mm],
        )
    ]
    footer_content[0].setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    keep_box.extend(footer_content)

    story.append(KeepTogether(keep_box))
    return story


def extract_story_text(story: list[Any]) -> str:
    """Recursively extract plain text strings from Platypus story flowables."""
    texts: list[str] = []

    def _walk(item: Any) -> None:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, Paragraph):
            texts.append(item.text)
        elif isinstance(item, Table):
            for row in item._cellvalues:
                for cell in row:
                    _walk(cell)
        elif hasattr(item, "_content") and isinstance(item._content, (list, tuple)):
            for sub in item._content:
                _walk(sub)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                _walk(sub)

    for flowable in story:
        _walk(flowable)
    return "\n".join(texts)


def generate_pdf(data: ReportData, output_path: str | Path) -> Path:
    """Generate the immutable PDF report and return its path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=data.title,
        author="ThermoGuard AI",
    )
    story = build_report_story(data)
    doc.build(story)

    fingerprint = data.snapshot_sha256[:20] if data.snapshot_sha256 else "UNVERIFIED"
    logger.info("PDF report written to %s (Snapshot SHA: %s)", out, fingerprint)
    return out

