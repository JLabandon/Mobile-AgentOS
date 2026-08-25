from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..graph_space.schema import ArtifactDraft
from ..graph_space.registry import RegistryTable
from ..model_clients.base import ScreenModelClient
from .agent import Completed, ExecutionContext, Failed, NeedsExpansion


@dataclass(frozen=True)
class Observation:
    screenshot_path: Path
    visible_context: str = ""


class UiDriver(Protocol):
    def observe(self, app_id: str) -> Observation:
        ...

    def act(self, app_id: str, action: dict[str, Any]) -> None:
        ...

    def settle(self, app_id: str) -> None:
        ...


class UiLoopExecutor:
    """Generic observe-think-act loop. App-specific logic lives in model decisions and profiles."""

    def __init__(self, driver: UiDriver, model: ScreenModelClient, registry: RegistryTable, *, max_steps: int = 8) -> None:
        self.driver = driver
        self.model = model
        self.registry = registry
        self.max_steps = max_steps

    def execute(self, context: ExecutionContext) -> Completed | Failed | NeedsExpansion:
        memory = self._context_text(context)
        action_history: list[dict[str, Any]] = []
        for step in range(self.max_steps):
            observation = self.driver.observe(context.profile.app_id)
            action = self.model.decide_ui_action(
                screenshot_path=observation.screenshot_path,
                agent_name=context.profile.app_id,
                app_label=context.profile.label,
                task_instruction=context.work_goal,
                memory=(
                    f"{memory}\n"
                    f"Execution history for this work unit: {action_history}\n"
                    f"Structured visible UI: {observation.visible_context}"
                ),
            )
            kind = str(action.get("action", "")).lower()
            if kind == "complete":
                payload = action.get("artifact", {"message": str(action.get("message", "completed"))})
                work = context.snapshot.work(context.assignment.work_id)
                expected_nodes = tuple(context.snapshot.artifact(item) for item in work.output_artifact_ids)
                expected = tuple(item.kind for item in expected_nodes)
                artifact_kind = str(action.get("artifact_kind", "")).strip()
                if not artifact_kind and len(expected) == 1:
                    artifact_kind = expected[0]
                if expected and artifact_kind not in expected:
                    action_history.append(self._protocol_error("completion artifact kind does not satisfy the assigned work contract"))
                    continue
                if not self._valid_completion_payload(payload):
                    action_history.append(self._protocol_error("complete requires an artifact with non-empty value and evidence"))
                    continue
                matching_ids = [item.node_id for item in expected_nodes if item.kind == artifact_kind]
                artifact_node_id = str(action.get("artifact_node_id", "")).strip() or None
                if artifact_node_id is not None and artifact_node_id not in matching_ids:
                    action_history.append(self._protocol_error("artifact_node_id does not match an expected output"))
                    continue
                if artifact_node_id is None and len(matching_ids) == 1:
                    artifact_node_id = matching_ids[0]
                if artifact_node_id is None and len(matching_ids) > 1:
                    action_history.append(self._protocol_error("multiple expected outputs share this kind; select artifact_node_id"))
                    continue
                return Completed((ArtifactDraft(artifact_kind or "work_result", payload if isinstance(payload, dict) else {"message": str(payload)}, (str(observation.screenshot_path),), artifact_node_id),))
            if kind == "fail":
                return Failed(str(action.get("message", "model reported failure")))
            if kind in {"request_information", "request_operation"}:
                capability = str(action.get("required_capability", "")).strip()
                providers = self.registry.providers(capability)
                if not capability:
                    action_history.append(self._protocol_error("runtime request requires required_capability"))
                    continue
                if not providers:
                    action_history.append(self._protocol_error("no Registry provider offers the requested capability"))
                    continue
                target = str(action.get("target_agent", "")).strip()
                if not target and len(providers) == 1:
                    target = providers[0].app_id
                if not target:
                    choices = [profile.app_id for profile in providers]
                    action_history.append(self._protocol_error(f"multiple providers are available; select target_agent from {choices}"))
                    continue
                if target not in {profile.app_id for profile in providers}:
                    action_history.append(self._protocol_error("target_agent does not provide required_capability"))
                    continue
                need = str(action.get("need", action.get("operation", ""))).strip()
                if not need:
                    action_history.append(self._protocol_error("runtime request requires need"))
                    continue
                raw_identity = action.get("artifact_identity")
                identity = None
                if isinstance(raw_identity, dict):
                    try:
                        identity = self.registry.decode_artifact_identity(
                            str(raw_identity.get("schema_id", "")),
                            raw_identity.get("parameters"),
                        )
                    except (KeyError, ValueError) as exc:
                        action_history.append(self._protocol_error(f"invalid artifact_identity: {exc}"))
                        continue
                return NeedsExpansion(
                    checkpoint=ArtifactDraft("execution_checkpoint", {"goal": context.work_goal, "step": step, "request": action}, (str(observation.screenshot_path),)),
                    provider_agent_id=target,
                    required_capability=capability,
                    provider_goal=need,
                    artifact_kind="information_result" if kind == "request_information" else "operation_result",
                    continuation_goal=f"Continue: {context.work_goal}",
                    identity=identity,
                    request_kind="information" if kind == "request_information" else "operation",
                )
            if kind not in {"click", "input_text", "swipe", "back"}:
                action_history.append(self._protocol_error(f"unsupported action: {kind or '<empty>'}"))
                continue
            self.driver.act(context.profile.app_id, action)
            action_history.append(self._action_record(action))
            self.driver.settle(context.profile.app_id)
        return Failed("work unit exceeded max UI steps")

    @staticmethod
    def _action_record(action: dict[str, Any]) -> dict[str, Any]:
        fields = ("action", "element_id", "x", "y", "text", "direction")
        return {field: action[field] for field in fields if field in action}

    @staticmethod
    def _protocol_error(message: str) -> dict[str, str]:
        return {"action": "protocol_error", "message": message}

    @staticmethod
    def _valid_completion_payload(payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        value = payload.get("value")
        evidence = payload.get("evidence")
        if value is None or value == "" or value == [] or value == {}:
            return False
        return isinstance(evidence, list) and bool(evidence)

    def _context_text(self, context: ExecutionContext) -> str:
        inputs = [{"kind": artifact.kind, "payload": artifact.payload} for artifact in context.input_artifacts]
        work = context.snapshot.work(context.assignment.work_id)
        expected = [
            {"artifact_node_id": item, "kind": context.snapshot.artifact(item).kind}
            for item in work.output_artifact_ids
        ]
        return str(
            {
                "assigned_goal": context.work_goal,
                "expected_artifacts": expected,
                "input_artifacts": inputs,
                "long_term_memory": context.profile.long_term_memory,
                "registry": self.registry.prompt_rows(),
                "artifact_schemas": self.registry.artifact_schema_rows(),
            }
        )
