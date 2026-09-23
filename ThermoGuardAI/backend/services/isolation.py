"""Shared organization-isolation helpers for device-linked records (S2.1 + S4).

Ownership chains enforced here:
    Organization → Device → Inspection → Report
    Organization → Device → Inspection → Maintenance
    Organization → Panel → Component            (S4: panels carry ownership)

Organization is always DERIVED through existing relationships — device for
inspection-linked records, panel for components — no organization column is
duplicated on records (per the S2.1 instruction "do not duplicate organization
fields unnecessarily").

Legacy-data policy (identical across report/maintenance/panel/component):
records that carry no ownership (no inspection, a device-less inspection, or a
panel with ``organization`` NULL) are treated as legacy data and remain visible
to all authenticated users — exactly matching the established policy for the
42 legacy device-less inspections. Records linked to an organization are
strictly scoped to that organization (NULL matches NULL); a cross-organization
access attempt returns a not-found response so record existence is never
revealed.
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.core.exceptions import NotFoundError
from backend.models.alarm import Alarm
from backend.models.component import Component
from backend.models.device import Device
from backend.models.incident import Incident
from backend.models.inspection import Inspection
from backend.models.maintenance import MaintenanceRecord
from backend.models.panel import Panel
from backend.models.report import Report


def organization_condition(organization: str | None):
    """Device org-matching condition (NULL matches NULL)."""
    if organization is None:
        return Device.organization.is_(None)
    return Device.organization == organization


def report_ownership(db: Session, report_id: int) -> tuple[bool, str | None]:
    """(is_device_linked, organization) for a report via Report → Inspection → Device.

    ``is_device_linked=False`` means the report is unowned legacy data (no
    inspection, or an inspection with no device) and stays visible to all
    authenticated users. When a report IS device-linked, ownership follows
    strict NULL-matching — a device with a NULL organization is only visible
    to organization-less users — so list and detail access never diverge.
    """
    row = db.execute(
        select(Device.organization)
        .select_from(Report)
        .join(Inspection, Report.inspection_id == Inspection.id)
        .join(Device, Inspection.device_id == Device.id)
        .where(Report.id == report_id)
    ).first()
    return (True, row[0]) if row else (False, None)


def maintenance_ownership(db: Session, record_id: int) -> tuple[bool, str | None]:
    """(is_device_linked, organization) via Record → Inspection → Device."""
    row = db.execute(
        select(Device.organization)
        .select_from(MaintenanceRecord)
        .join(Inspection, MaintenanceRecord.inspection_id == Inspection.id)
        .join(Device, Inspection.device_id == Device.id)
        .where(MaintenanceRecord.id == record_id)
    ).first()
    return (True, row[0]) if row else (False, None)


def report_scope_condition(organization: str | None):
    """List filter for reports: unowned (legacy) OR caller's own organization."""
    org_condition = organization_condition(organization)
    return or_(
        Report.inspection_id.is_(None),
        Report.inspection.has(Inspection.device_id.is_(None)),
        Report.inspection.has(Inspection.device.has(org_condition)),
    )


def maintenance_scope_condition(organization: str | None):
    """List filter for maintenance records: unowned OR caller's own organization."""
    org_condition = organization_condition(organization)
    return or_(
        MaintenanceRecord.inspection_id.is_(None),
        MaintenanceRecord.inspection.has(Inspection.device_id.is_(None)),
        MaintenanceRecord.inspection.has(Inspection.device.has(org_condition)),
    )


def ensure_report_accessible(db: Session, report_id: int, organization: str | None) -> Report:
    """Fetch a report only if it belongs to the caller's organization.

    Cross-organization and nonexistent IDs both raise NotFoundError — a direct
    ID guess can never reveal another organization's report. Device-linked
    reports require an exact organization match (NULL matches NULL); unowned
    legacy reports (no inspection / device-less inspection) stay accessible to
    all authenticated users.
    """
    report = db.get(Report, report_id)
    if report is None:
        raise NotFoundError(f"Report {report_id} not found")
    is_linked, org = report_ownership(db, report_id)
    if is_linked and org != organization:
        raise NotFoundError(f"Report {report_id} not found")
    return report


def ensure_maintenance_accessible(db: Session, record_id: int, organization: str | None) -> MaintenanceRecord:
    """Fetch a maintenance record only if it belongs to the caller's organization."""
    record = db.get(MaintenanceRecord, record_id)
    if record is None:
        raise NotFoundError(f"Maintenance record {record_id} not found")
    is_linked, org = maintenance_ownership(db, record_id)
    if is_linked and org != organization:
        raise NotFoundError(f"Maintenance record {record_id} not found")
    return record


def ensure_inspection_for_organization(db: Session, inspection_id: int, organization: str | None) -> Inspection:
    """Validate an inspection before creating device-linked records for it.

    An inspection linked to another organization's device is treated as not
    found; device-less (legacy) inspections remain accessible to everyone.
    """
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise NotFoundError(f"Inspection {inspection_id} not found")
    if inspection.device is not None and inspection.device.organization != organization:
        raise NotFoundError(f"Inspection {inspection_id} not found")
    return inspection


# ---------------------------------------------------------------------------
# S4: panels and components (panel organization ownership)
# ---------------------------------------------------------------------------
def panel_scope_condition(organization: str | None):
    """List filter for panels: legacy (NULL-org) OR the caller's own organization.

    ``== None`` on an ORM column compiles to IS NULL, so org-less users see
    only legacy panels — the same NULL-matching used everywhere else.
    """
    return or_(Panel.organization.is_(None), Panel.organization == organization)


def ensure_panel_accessible(db: Session, panel_id: int, organization: str | None) -> Panel:
    """Fetch a panel only if it belongs to the caller's organization.

    Legacy panels (NULL organization — e.g. the seeded PANEL-MAIN and every
    pre-S4 panel) are platform-level and visible to all authenticated users.
    Org-scoped panels require an exact organization match; a foreign panel
    looks identical to a nonexistent one (404, no existence leak).
    """
    panel = db.get(Panel, panel_id)
    if panel is None:
        raise NotFoundError(f"Panel {panel_id} not found")
    if panel.organization is not None and panel.organization != organization:
        raise NotFoundError(f"Panel {panel_id} not found")
    return panel


def ensure_component_accessible(db: Session, component_id: int, organization: str | None) -> Component:
    """Fetch a component only if its panel belongs to the caller's organization.

    Closes the S2.1 gap where a maintenance record created with only
    ``component_id`` could not derive organization ownership. Ownership is
    derived Component → Panel.organization; legacy panels stay accessible to
    everyone, org-scoped panels require an exact match.
    """
    component = db.get(Component, component_id)
    if component is None:
        raise NotFoundError(f"Component {component_id} not found")
    panel = db.get(Panel, component.panel_id)
    if panel is not None and panel.organization is not None and panel.organization != organization:
        raise NotFoundError(f"Component {component_id} not found")
    return component


# ---------------------------------------------------------------------------
# S4: alarms (Organization → Device → Inspection → Alarm)
# ---------------------------------------------------------------------------
def alarm_scope_condition(organization: str | None):
    """List filter for alarms: unowned OR caller's own organization."""
    org_condition = organization_condition(organization)
    return or_(
        Alarm.inspection_id.is_(None),
        Alarm.inspection.has(Inspection.device_id.is_(None)),
        Alarm.inspection.has(Inspection.device.has(org_condition)),
    )


def ensure_alarm_accessible(db: Session, alarm_id: int, organization: str | None) -> Alarm:
    """Fetch an alarm only if it belongs to the caller's organization.

    Alarms from legacy device-less inspections stay accessible to everyone;
    device-linked alarms require an exact organization match (404 otherwise).
    """
    alarm = db.get(Alarm, alarm_id)
    if alarm is None:
        raise NotFoundError(f"Alarm {alarm_id} not found")
    if alarm.inspection is not None and alarm.inspection.device is not None:
        if alarm.inspection.device.organization != organization:
            raise NotFoundError(f"Alarm {alarm_id} not found")
    return alarm
