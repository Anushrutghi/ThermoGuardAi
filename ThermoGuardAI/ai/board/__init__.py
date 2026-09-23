"""Switchboard-only inspection engine.

This package replaces the old multi-class electrical component detection
(circuit breakers, fuses, relays, busbars, terminals…) with a single job:

    find the switch board / distribution board in the frame — any type,
    any size — then analyse heat distribution and wiring faults across it,
    and explain the result in plain language.

Modules
-------
``board_detector``  single-class switchboard detector (classical CV, no weights)
``heat_risk``       RGB heat-risk index + zone grid (never a temperature claim)
``wire_faults``     conservative wiring / board fault heuristics
``plain_language``  turns findings into sentences a non-technical person reads
``session``         per-scan state machine driving the browser experience
"""
from __future__ import annotations

from ai.board.board_detector import BoardCandidate, BoardDetector
from ai.board.heat_risk import HeatRiskAnalyzer, HeatRiskResult, HeatZone

__all__ = [
    "BoardCandidate",
    "BoardDetector",
    "HeatRiskAnalyzer",
    "HeatRiskResult",
    "HeatZone",
]
