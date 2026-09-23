"""RGB-only inspection mode: detect visible faults with classical CV.

These heuristics let the platform inspect panels with a plain webcam or
phone camera when no thermal camera is present. Each check returns
findings with a severity and confidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ai.detector.base import HealthStatus


@dataclass
class VisualFinding:
    """A visible fault detected in the RGB frame."""

    fault_type: str
    severity: HealthStatus
    confidence: float
    bbox: tuple[int, int, int, int]
    message: str


@dataclass
class VisualInspectionResult:
    findings: list[VisualFinding] = field(default_factory=list)

    @property
    def worst_severity(self) -> HealthStatus:
        order = {HealthStatus.HEALTHY: 0, HealthStatus.WARNING: 1, HealthStatus.HIGH_RISK: 2, HealthStatus.CRITICAL: 3}
        return max((f.severity for f in self.findings), key=lambda s: order[s], default=HealthStatus.HEALTHY)


class VisualFaultDetector:
    """Runs the full set of RGB fault heuristics on a frame."""

    def analyze(self, frame: np.ndarray) -> VisualInspectionResult:
        findings: list[VisualFinding] = []
        findings.extend(_detect_burn_marks(frame))
        findings.extend(_detect_sparks(frame))
        findings.extend(_detect_smoke(frame))
        findings.extend(_detect_water_intrusion(frame))
        findings.extend(_detect_discoloration(frame))
        findings.extend(_detect_loose_wires(frame))
        return VisualInspectionResult(findings=findings)


def _detect_burn_marks(frame: np.ndarray) -> list[VisualFinding]:
    """Dark, charred regions — often burn marks around terminals."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(gray, 40, 255, cv2.THRESH_BINARY_INV)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    findings: list[VisualFinding] = []
    h, w = frame.shape[:2]
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw * ch < (h * w) * 0.004 or cw < 15 or ch < 15:
            continue
        roi = gray[y : y + ch, x : x + cw]
        mean_dark = float(np.mean(roi))
        if mean_dark > 60:  # not dark enough to be char
            continue
        findings.append(
            VisualFinding(
                fault_type="burn_mark",
                severity=HealthStatus.HIGH_RISK,
                confidence=float(np.clip(0.5 + (60 - mean_dark) / 60 * 0.4, 0.5, 0.95)),
                bbox=(x, y, x + cw, y + ch),
                message="Burn marks detected — possible arcing or overheating.",
            )
        )
    return findings


def _detect_sparks(frame: np.ndarray) -> list[VisualFinding]:
    """Very bright, saturated regions — possible visible sparks/arcing."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    bright = cv2.inRange(hsv, (0, 0, 230), (255, 255, 255))
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    findings: list[VisualFinding] = []
    h, w = frame.shape[:2]
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw * ch < (h * w) * 0.0006 or cw < 8 or ch < 8:
            continue
        findings.append(
            VisualFinding(
                fault_type="visible_spark",
                severity=HealthStatus.CRITICAL,
                confidence=float(np.clip(0.6 + min(cw, ch) / 200, 0.6, 0.95)),
                bbox=(x, y, x + cw, y + ch),
                message="Bright flash detected — possible arcing. Inspect immediately.",
            )
        )
    return findings


def _detect_smoke(frame: np.ndarray) -> list[VisualFinding]:
    """Gray, low-saturation haze — smoke indicators."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    grayish = ((sat < 40) & (val > 60) & (val < 210)).astype(np.uint8) * 255
    grayish = cv2.morphologyEx(grayish, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(grayish, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    findings: list[VisualFinding] = []
    h, w = frame.shape[:2]
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw * ch < (h * w) * 0.02 or cw > w * 0.95:
            continue
        findings.append(
            VisualFinding(
                fault_type="smoke_haze",
                severity=HealthStatus.CRITICAL,
                confidence=0.55,
                bbox=(x, y, x + cw, y + ch),
                message="Smoke-like haze detected — potential insulation burn.",
            )
        )
    return findings


def _detect_water_intrusion(frame: np.ndarray) -> list[VisualFinding]:
    """Dark, glossy streaks with reflections — possible water ingress."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    # water stains often show as bright specular reflections
    specular = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), (0, 0, 200), (255, 40, 255))
    combined = cv2.bitwise_and(specular, cv2.dilate(edges, np.ones((3, 3), np.uint8)))
    if int(np.sum(combined > 0)) > 4000:
        x, y, cw, ch = cv2.boundingRect(combined)
        return [
            VisualFinding(
                fault_type="water_intrusion",
                severity=HealthStatus.HIGH_RISK,
                confidence=0.6,
                bbox=(x, y, x + cw, y + ch),
                message="Specular reflections suggest possible water intrusion.",
            )
        ]
    return []


def _detect_discoloration(frame: np.ndarray) -> list[VisualFinding]:
    """High-hue variance regions — overheating discoloration on plastics."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float32)
    # Overheated plastics shift to brown/dark; detect large low-saturation warm zones
    mask = ((hsv[:, :, 2] < 180) & (hsv[:, :, 1] < 90)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    findings: list[VisualFinding] = []
    h, w = frame.shape[:2]
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw * ch < (h * w) * 0.03 or cw * ch > (h * w) * 0.7:
            continue
        roi = hue[y : y + ch, x : x + cw]
        if float(np.std(roi)) > 22:
            findings.append(
                VisualFinding(
                    fault_type="discoloration",
                    severity=HealthStatus.WARNING,
                    confidence=0.55,
                    bbox=(x, y, x + cw, y + ch),
                    message="Material discoloration detected — possible heat damage.",
                )
            )
    return findings


def _detect_loose_wires(frame: np.ndarray) -> list[VisualFinding]:
    """Thin linear structures protruding outside component regions."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=60, maxLineGap=12)
    findings: list[VisualFinding] = []
    if lines is None:
        return findings
    h, w = frame.shape[:2]
    for line in lines[:8]:
        # cv2.HoughLinesP returns (N,1,4) on OpenCV 4.x but flat (N,4) on 5.x —
        # ravel() normalises so int() unpacking works on both.
        x1, y1, x2, y2 = (int(v) for v in line.ravel())
        if abs(x2 - x1) + abs(y2 - y1) < 60:
            continue
        # wire dangling near bottom or edges of frame
        if y1 > h * 0.75 or y2 > h * 0.75:
            findings.append(
                VisualFinding(
                    fault_type="loose_wire",
                    severity=HealthStatus.WARNING,
                    confidence=0.5,
                    bbox=(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)),
                    message="Unsecured wire detected — verify termination.",
                )
            )
    return findings
