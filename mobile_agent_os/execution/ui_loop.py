from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..graph_space.models import ArtifactDraft
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
                task_instruction=context.node_goal,
                memory=(
                    f"{memory}\n"
                    f"Execution history for this work unit: {action_history}\n"
                    f"Structured visible UI: {observation.visible_context}"
                ),
            )
            kind = str(action.get("action", "")).lower()
            if kind == "complete":
                payload = action.get("artifact", {"message": str(action.get("message", "completed"))})
                expected = context.snapshot.node(context.assignment.node_id).expected_artifact_kinds
                artifact_kind = str(action.get("artifact_kind", "")).strip()
                if not artifact_kind and len(expected) == 1:
                    artifact_kind = expected[0]
                if expected and artifact_kind not in expected:
                    return Failed("completion artifact kind does not satisfy assigned work contract")
                if not self._valid_completion_payload(payload):
                    report = self.model.decide_completion_report(
                        screenshot_path=observation.screenshot_path,
                        agent_name=context.profile.app_id,
                        app_label=context.profile.label,
                        task_instruction=context.node_goal,
                        artifact_kind=artifact_kind or "work_result",
                        memory=memory + f"\nStructured visible UI: {observation.visible_context}",
                    )
                    artifact_kind = str(report.get("artifact_kind", artifact_kind)).strip() or artifact_kind
                    payload = report.get("artifact", {})
                return Completed((ArtifactDraft(artifact_kind or "work_result", payload if isinstance(payload, dict) else {"message": str(payload)}, (str(observation.screenshot_path),)),))
            if kind == "fail":
                return Failed(str(action.get("message", "model reported failure")))
            if kind in {"request_information", "request_operation"}:
                capability = str(action.get("required_capability", "")).strip()
                providers = self.registry.providers(capability)
                if not capability or not providers:
                    return Failed("runtime request named no registry capability")
                target = str(action.get("target_agent", providers[0].app_id))
                if target not in {profile.app_id for profile in providers}:
                    return Failed("runtime request target does not provide requested capability")
                return NeedsExpansion(
                    checkpoint=ArtifactDraft("execution_checkpoint", {"goal": context.node_goal, "step": step, "request": action}, (str(observation.screenshot_path),)),
                    provider_agent_id=target,
                    required_capability=capability,
                    provider_goal=str(action.get("provider_goal", action.get("need", action.get("operation", "Provide the requested result.")))),
                    provider_artifact_kinds=("information_result" if kind == "request_information" else "operation_result",),
                    continuation_goal=f"Continue: {context.node_goal}",
                    request_kind="information" if kind == "request_information" else "operation",
                )
            if kind not in {"click", "input_text", "swipe", "back"}:
                return Failed(f"unsupported model action: {kind or '<empty>'}")
            self.driver.act(context.profile.app_id, action)
            action_history.append(self._action_record(action))
            self.driver.settle(context.profile.app_id)
        return Failed("work unit exceeded max UI steps")

    @staticmethod
    def _action_record(action: dict[str, Any]) -> dict[str, Any]:
        fields = ("action", "element_id", "x", "y", "text", "direction")
        return {field: action[field] for field in fields if field in action}

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
        expected = context.snapshot.node(context.assignment.node_id).expected_artifact_kinds
        return str(
            {
                "assigned_goal": context.node_goal,
                "expected_artifact_kinds": list(expected),
                "input_artifacts": inputs,
                "long_term_memory": context.profile.long_term_memory,
                "registry": self.registry.prompt_rows(),
            }
        )
