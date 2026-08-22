from pathlib import Path

from mobile_agent_os.execution import ExecutionContext, UiLoopExecutor
from mobile_agent_os.graph_space import AppProfile, GraphSnapshot, Node, NodeKind
from mobile_agent_os.scheduling import Assignment

from tests.fakes.runtime import registry


class Driver:
    def __init__(self, screenshot: Path) -> None:
        self.screenshot = screenshot
        self.actions = []
        self.settles = 0

    def observe(self, app_id: str):
        from mobile_agent_os.execution.ui_loop import Observation

        return Observation(self.screenshot, f"visible {app_id}")

    def act(self, app_id: str, action: dict) -> None:
        self.actions.append((app_id, action))

    def settle(self, app_id: str) -> None:
        self.settles += 1


class Model:
    def __init__(self) -> None:
        self.responses = [{"action": "click", "x": 10, "y": 20}, {"action": "complete", "artifact_kind": "work_result", "artifact": {"value": "done", "evidence": ["visible completion state"]}}]

    def decide_ui_action(self, **kwargs):
        return self.responses.pop(0)


def test_ui_loop_uses_only_primitive_actions_before_completion(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")
    driver = Driver(image)
    context = ExecutionContext(
        Assignment("AS1", "g", "N", "calendar", 1, ()),
        "Create event",
        GraphSnapshot("g", 1, (Node("N", NodeKind.WORK, "calendar", "Create event"),), (), ()),
        registry().get("calendar"),
        (),
    )
    result = UiLoopExecutor(driver, Model(), registry()).execute(context)
    assert driver.actions == [("calendar", {"action": "click", "x": 10, "y": 20})]
    assert driver.settles == 1
    assert result.artifacts[0].kind == "work_result"


def test_ui_loop_uses_single_expected_artifact_kind_when_model_omits_it(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")
    driver = Driver(image)

    class CompleteModel:
        def decide_ui_action(self, **kwargs):
            del kwargs
            return {"action": "complete", "artifact": {"value": "Googleplex", "evidence": ["Appointment note: Googleplex"]}}

    context = ExecutionContext(
        Assignment("AS1", "g", "N", "calendar", 1, ()),
        "Create event",
        GraphSnapshot("g", 1, (Node("N", NodeKind.WORK, "calendar", "Create event", expected_artifact_kinds=("appointment_details",)),), (), ()),
        registry().get("calendar"),
        (),
    )
    result = UiLoopExecutor(driver, CompleteModel(), registry()).execute(context)
    assert result.artifacts[0].kind == "appointment_details"


def test_ui_loop_repairs_malformed_completion_with_a_protocol_report(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")
    driver = Driver(image)

    class CompleteModel:
        def decide_ui_action(self, **kwargs):
            del kwargs
            return {"action": "complete", "artifact_kind": "appointment_details", "artifact": {}}

        def decide_completion_report(self, **kwargs):
            assert kwargs["artifact_kind"] == "appointment_details"
            return {"artifact_kind": "appointment_details", "artifact": {"value": "Googleplex", "evidence": ["Visible note"]}}

    context = ExecutionContext(
        Assignment("AS1", "g", "N", "calendar", 1, ()),
        "Create event",
        GraphSnapshot("g", 1, (Node("N", NodeKind.WORK, "calendar", "Create event", expected_artifact_kinds=("appointment_details",)),), (), ()),
        registry().get("calendar"),
        (),
    )
    result = UiLoopExecutor(driver, CompleteModel(), registry()).execute(context)
    assert result.artifacts[0].payload["value"] == "Googleplex"
