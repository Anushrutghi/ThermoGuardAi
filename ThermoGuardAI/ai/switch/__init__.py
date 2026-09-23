"""Switch-first inspection package (S1).

The camera finds an electrical wall switch, confirms it across consecutive
frames, locks an inspection ROI, then runs a stabilized thermal scan with
conservative anomaly classification.
"""
from ai.switch.controller import SwitchInspectionController, draw_switch_overlay
from ai.switch.switch_detector import SwitchCandidate, SwitchDetector

__all__ = ["SwitchCandidate", "SwitchDetector", "SwitchInspectionController", "draw_switch_overlay"]
