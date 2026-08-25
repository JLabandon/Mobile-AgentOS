from pathlib import Path

from mobile_agent_os.execution import ExecutionContext, NeedsExpansion, UiLoopExecutor
from mobile_agent_os.graph_space import AppProfile, ArtifactNode, ArtifactState, GlobalGraphSnapshot, RegistryTable, WorkNode
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


def _context(kind: str | None = None) -> ExecutionContext:
    artifact = ArtifactNode("A1", kind, ArtifactState.FUTURE, "W1") if kind else None
    work = WorkNode("W1", "calendar", "Create event", output_artifact_ids=("A1",) if kind else ())
    snapshot = GlobalGraphSnapshot(1, (work,), (artifact,) if artifact else (), (), ())
    return ExecutionContext(
        Assignment("AS1", "W1", "calendar", 1, ("task",), ()),
        "Create event",
        snapshot,
        registry().get("calendar"),
        (),
    )


def test_ui_loop_uses_only_primitive_actions_before_completion(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")
    driver = Driver(image)

    class Model:
        def __init__(self) -> None:
            self.responses = [
                {"action": "click", "x": 10, "y": 20},
                {"action": "complete", "artifact_kind": "work_result", "artifact": {"value": "done", "evidence": ["visible completion state"]}},
            ]

        def decide_ui_action(self, **kwargs):
            del kwargs
            return self.responses.pop(0)

    result = UiLoopExecutor(driver, Model(), registry()).execute(_context())
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

    result = UiLoopExecutor(driver, CompleteModel(), registry()).execute(_context("appointment_details"))
    assert result.artifacts[0].kind == "appointment_details"
    assert result.artifacts[0].artifact_node_id == "A1"


def test_ui_loop_retries_malformed_completion_through_the_action_protocol(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")
    driver = Driver(image)

    class CompleteModel:
        def __init__(self) -> None:
            self.memories = []
            self.responses = [
                {"action": "complete", "artifact_kind": "appointment_details", "artifact": {}},
                {
                    "action": "complete",
                    "artifact_kind": "appointment_details",
                    "artifact": {"value": "Googleplex", "evidence": ["Visible note"]},
                },
            ]

        def decide_ui_action(self, **kwargs):
            self.memories.append(kwargs["memory"])
            return self.responses.pop(0)

    model = CompleteModel()
    result = UiLoopExecutor(driver, model, registry()).execute(_context("appointment_details"))
    assert result.artifacts[0].payload["value"] == "Googleplex"
    assert "protocol_error" in model.memories[1]


def test_ui_loop_resolves_a_unique_request_provider_and_decodes_identity(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")

    class RequestModel:
        def decide_ui_action(self, **kwargs):
            del kwargs
            return {
                "action": "request_information",
                "required_capability": "retrieve_information",
                "need": "Retrieve the requested appointment location.",
                "artifact_identity": {
                    "schema_id": "appointment.location",
                    "parameters": [
                        {"name": "participant", "value": "Alice"},
                        {"name": "start_time", "value": "2026-08-26T15:00:00+08:00"},
                    ],
                },
            }

    result = UiLoopExecutor(Driver(image), RequestModel(), registry()).execute(_context())
    assert isinstance(result, NeedsExpansion)
    assert result.provider_agent_id == "notes"
    assert result.identity is not None
    assert result.identity.parameters["participant"] == "Alice"


def test_ui_loop_requires_an_explicit_target_when_multiple_providers_match(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-read-by-fake-model")
    base = registry()
    profiles = {profile.app_id: profile for profile in base.profiles()}
    profiles["archive"] = AppProfile(
        "archive",
        "Archive",
        "Document archive",
        ("retrieve_information",),
        ("archive.pkg",),
    )
    ambiguous_registry = RegistryTable(
        profiles,
        {schema.schema_id: schema for schema in base.artifact_schemas()},
    )

    class RequestModel:
        def __init__(self) -> None:
            self.memories = []
            self.responses = [
                {
                    "action": "request_information",
                    "required_capability": "retrieve_information",
                    "need": "Retrieve the requested information.",
                },
                {
                    "action": "request_information",
                    "required_capability": "retrieve_information",
                    "target_agent": "notes",
                    "need": "Retrieve the requested information.",
                },
            ]

        def decide_ui_action(self, **kwargs):
            self.memories.append(kwargs["memory"])
            return self.responses.pop(0)

    model = RequestModel()
    result = UiLoopExecutor(Driver(image), model, ambiguous_registry).execute(_context())
    assert isinstance(result, NeedsExpansion)
    assert result.provider_agent_id == "notes"
    assert "multiple providers" in model.memories[1]
