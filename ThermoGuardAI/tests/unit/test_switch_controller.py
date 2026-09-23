"""S1 — switch-first controller (state machine) unit tests."""
from __future__ import annotations

from ai.switch.controller import (
    INSPECTION_COMPLETE,
    SEARCHING_FOR_SWITCH,
    SWITCH_DETECTED,
    SwitchInspectionController,
)
from tests.switch_test_utils import (
    FastSettings,
    NoThermalSource,
    RampedThermalSource,
    make_plain_wall,
    make_switch_frame,
)


def test_controller_reaches_complete() -> None:
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=RampedThermalSource(delta=22.0))
    frame = make_switch_frame()
    status = None
    for _ in range(20):  # 3 stable + 2 baseline + 3 scan + margin
        status = ctrl.on_frame(frame)
    assert ctrl.done, ctrl.state
    assert ctrl.state == INSPECTION_COMPLETE
    assert status is not None
    assert status["complete"] is not None
    assert status["complete"]["thermal_available"]
    assert status["complete"]["classification"] != "UNAVAILABLE"
    assert ctrl.switch_bbox is not None and ctrl.roi is not None
    assert status["progress"] == 100


def test_controller_requires_stable_frames() -> None:
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=RampedThermalSource())
    frame = make_switch_frame()
    for _ in range(FastSettings.switch_stable_frames - 1):
        status = ctrl.on_frame(frame)
    assert not ctrl.confirmed, "one less than the required frames must not confirm"
    assert status["state"] == SWITCH_DETECTED
    assert status["stable_frames"] == FastSettings.switch_stable_frames - 1
    # one more frame → confirmed
    ctrl.on_frame(frame)
    assert ctrl.confirmed


def test_controller_resets_on_unstable_detection() -> None:
    """A switch that appears then disappears must reset the confirmation count."""
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=RampedThermalSource())
    frame = make_switch_frame()
    for _ in range(2):
        ctrl.on_frame(frame)
    assert ctrl.stable_count == 2
    # switch vanishes → back to searching
    status = ctrl.on_frame(make_plain_wall())
    assert ctrl.stable_count == 0
    assert ctrl.state == SEARCHING_FOR_SWITCH
    assert status["guidance"] is None
    # and it must NOT be confirmed
    assert not ctrl.confirmed


def test_controller_search_message() -> None:
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=RampedThermalSource())
    status = ctrl.on_frame(make_plain_wall())
    assert status["state"] == SEARCHING_FOR_SWITCH
    assert "switch" in status["message"].lower()


def test_controller_thermal_unavailable_is_honest() -> None:
    """No thermal data → honest UNAVAILABLE result, never faked temperatures."""
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=NoThermalSource())
    frame = make_switch_frame()
    for _ in range(40):
        status = ctrl.on_frame(frame)
    assert ctrl.done
    complete = status["complete"]
    assert complete is not None
    assert complete["thermal_available"] is False
    assert complete["classification"] == "UNAVAILABLE"
    assert complete["switch_temp"] is None
    assert "unavailable" in complete["message"].lower()


def test_emissivity_comes_from_active_source_not_global(monkeypatch) -> None:
    """S4 regression: the scan result records the ACTIVE source's emissivity.

    Even when the global thermal singleton reports a different emissivity, an
    inspection using an injected source must record that source's value — the
    global singleton is never authoritative while a session source exists.
    """
    # 1) inject source A with emissivity X
    source_a = RampedThermalSource(delta=22.0, emissivity=0.95)
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=source_a)
    frame = make_switch_frame()
    for _ in range(20):
        ctrl.on_frame(frame)
    assert ctrl.complete is not None
    assert ctrl.complete.emissivity == 0.95, "active source emissivity must be recorded"

    # 2) configure the global singleton to a DIFFERENT emissivity
    global_b = RampedThermalSource(delta=22.0, emissivity=0.6)
    monkeypatch.setattr("ai.thermal.factory.get_thermal_source", lambda: global_b)

    # 3) the already-completed inspection still records source A's emissivity
    assert ctrl.complete.emissivity == 0.95
    # and a NEW inspection using source A again also records it
    ctrl2 = SwitchInspectionController(settings=FastSettings(), thermal_source=source_a)
    for _ in range(20):
        ctrl2.on_frame(frame)
    assert ctrl2.complete is not None
    assert ctrl2.complete.emissivity == 0.95

    # 4) prove the patch is real AND that the global is authoritative ONLY
    # when no session source exists: a controller WITHOUT an injected source
    # must record the global's emissivity (0.6)
    ctrl3 = SwitchInspectionController(settings=FastSettings())
    for _ in range(20):
        ctrl3.on_frame(frame)
    assert ctrl3.complete is not None
    assert ctrl3.complete.emissivity == 0.6, "no session source → global is authoritative"


def test_analyzer_emissivity_explicit_and_recorded() -> None:
    """S4: analyzer records the emissivity passed for the session source."""
    from ai.switch.thermal import SwitchThermalAnalyzer

    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    source = RampedThermalSource(delta=22.0)
    result = None
    for _ in range(analyzer.baseline_frames + analyzer.scan_frames + 2):
        result = analyzer.add(source.read(), None, (0, 0, 480, 640), (480, 640), emissivity=0.93)
        if result is not None:
            break
    assert result is not None
    assert result.emissivity == 0.93


def test_controller_never_raises() -> None:
    """An exception on a bad frame must degrade to ERROR, not crash the loop."""
    ctrl = SwitchInspectionController(settings=FastSettings(), thermal_source=RampedThermalSource())
    status = ctrl.on_frame(None)  # type: ignore[arg-type]  # invalid input
    assert status["state"] == "ERROR"
    assert status["error"]
    # next good frame recovers
    status = ctrl.on_frame(make_switch_frame())
    assert status["state"] in (SEARCHING_FOR_SWITCH, SWITCH_DETECTED)
