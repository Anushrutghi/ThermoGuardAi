"""ORM model registry. Importing this package registers all tables."""
from backend.models.alarm import Alarm
from backend.models.component import Component
from backend.models.detection import Detection
from backend.models.device import Device
from backend.models.event import EventLog
from backend.models.fault import Fault
from backend.models.incident import Incident
from backend.models.inspection import Inspection
from backend.models.maintenance import MaintenanceRecord
from backend.models.panel import Panel
from backend.models.report import Report
from backend.models.temperature_history import TemperatureReading
from backend.models.thermal_history import ThermalHistory
from backend.models.user import User

__all__ = [
    "Alarm",
    "Component",
    "Detection",
    "Device",
    "EventLog",
    "Fault",
    "Incident",
    "Inspection",
    "MaintenanceRecord",
    "Panel",
    "Report",
    "TemperatureReading",
    "ThermalHistory",
    "User",
]
