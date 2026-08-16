from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .actions import ActionError, AgentAction
from .adb import AdbClient, AdbError
from .agent_memory import AgentMemoryStore
from .completion import (
    is_final_confirmation_action,
    normalized_match_text,
    requires_final_commit,
    requires_runtime_response,
    salient_received_information_terms,
    term_status_text as build_term_status_text,
    verify_completion as verify_completion_state,
    visible_final_confirmation_controls,
)
from .llm import DeepSeekClient, LlmError
from .prompts import app_profile_prompt, app_system_prompt, app_user_prompt
from .report import RunReporter
from .display import ActionResult
from .runtime_requests import (
    AgentRunResult,
    RuntimeInformationRequest,
    RuntimeInformationResponse,
    RuntimeOperationRequest,
    RuntimeOperationResponse,
)
from .snapshots import ObservationSnapshot
from .ui_tree import find_node, parse_ui_xml, prompt_snapshot, visible_texts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_STORE = AgentMemoryStore(PROJECT_ROOT)


@dataclass(frozen=True)
class AppConfig:
    name: str
    label: str
    package_candidates: list[str]
    launch: dict[str, Any]
    capabilities: tuple[str, ...] = ()
    description: str = ""
    task_guidelines: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubTask:
    agent_name: str
    instruction: str
    max_steps: int = 6
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    launch_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentStepResult:
    status: str
    action: AgentAction | None = None
    request: RuntimeInformationRequest | RuntimeOperationRequest | None = None
    message: str = ""


class AppStaffAgent:
    def __init__(self, *, config: AppConfig, adb: AdbClient, llm: DeepSeekClient, reporter: RunReporter) -> None:
        self.config = config
        self.adb = adb
        self.llm = llm
        self.reporter = reporter
        self.package_name: str | None = None
        self.received_information: list[RuntimeInformationResponse] = []
        self.received_operations: list[RuntimeOperationResponse] = []
        self.session_memory: list[str] = []
        self.long_term_memory = self._load_long_term_memory()
        self.available_peers: list[AppConfig] = []
        self._step_subtask: SubTask | None = None
        self._step_out_dir: Path | None = None
        self._step_index = 1
        self._step_launched = False
        self._step_done = False
        self._recent_action_keys: list[tuple[str, str | None, str | None, str | None]] = []
        self._agentos_nodes_by_snapshot: dict[str, list[Any]] = {}
        self._agentos_step_dir_by_snapshot: dict[str, Path] = {}
        self._agentos_latest_snapshot_id: str | None = None

    @property
    def name(self) -> str:
        return f"{self.config.name}_agent"

    def launch(self, subtask: SubTask | None = None) -> None:
        self.package_name = self.adb.pick_package(self.config.package_candidates)
        self.adb.force_stop(self.package_name)
        for attempt in range(2):
            self._launch_once(subtask)
            self.adb.settle(3.0)
            foreground = self.adb.foreground_package()
            if foreground == self.package_name:
                break
            self.reporter.event(
                "app_launch_retry",
                agent=self.name,
                package=self.package_name,
                foreground=foreground,
                expected=self.package_name,
                attempt=attempt + 1,
            )
        foreground = self.adb.foreground_package()
        self.reporter.event(
            "app_launch",
            agent=self.name,
            package=self.package_name,
            foreground=foreground,
            expected=self.package_name,
            matched=foreground == self.package_name,
        )
        if foreground != self.package_name:
            raise AdbError(f"{self.name} launch did not bring expected app to foreground: expected={self.package_name}, foreground={foreground}")

    def activate(self, subtask: SubTask | None = None) -> bool:
        if self.package_name is None:
            self.package_name = self.adb.pick_package(self.config.package_candidates)
        foreground = self.adb.foreground_package()
        if foreground == self.package_name:
            return False
        self.reporter.event("app_activate", agent=self.name, package=self.package_name, foreground=foreground)
        if self.config.launch.get("mode") == "intent":
            self.adb.force_stop(self.package_name)
            self.reporter.event("app_cold_resume", agent=self.name, package=self.package_name)
        self._launch_once(subtask)
        self.adb.settle(2.0)
        foreground = self.adb.foreground_package()
        if foreground == self.package_name:
            self.reporter.event("app_activate_complete", agent=self.name, package=self.package_name, foreground=foreground, method="profile")
            return True
        self.reporter.event("app_activate_retry", agent=self.name, package=self.package_name, foreground=foreground, method="launcher_then_profile")
        self.adb.launch_package(self.package_name)
        self.adb.settle(1.5)
        self._launch_once(subtask)
        self.adb.settle(2.0)
        foreground = self.adb.foreground_package()
        self.reporter.event("app_activate_complete", agent=self.name, package=self.package_name, foreground=foreground, method="launcher_then_profile")
        return True

    def _launch_once(self, subtask: SubTask | None = None) -> None:
        launch = self.config.launch
        if launch.get("mode") == "intent":
            args = [str(arg) for arg in launch.get("args", [])]
            if len(args) >= 2 and args[0] == "am" and args[1] == "start" and "-p" not in args:
                args = [*args[:2], "-p", self.package_name, *args[2:]]
            if subtask:
                args = [*args, *subtask.launch_args]
            self.adb.launch_shell(args)
        else:
            self.adb.launch_package(self.package_name)

    def system_prompt(self) -> str:
        return app_system_prompt()

    def profile_prompt(self) -> str:
        return app_profile_prompt(self)

    def user_prompt(
        self,
        *,
        subtask: SubTask,
        step: int,
        ui_text: str,
        term_status_text: str = "",
        incoming_request: RuntimeInformationRequest | RuntimeOperationRequest | None = None,
        blocked_action_text: str = "",
    ) -> str:
        return app_user_prompt(
            self,
            subtask=subtask,
            step=step,
            ui_text=ui_text,
            term_status_text=term_status_text,
            incoming_request=incoming_request,
            blocked_action_text=blocked_action_text,
        )

    def decide_action(
        self,
        *,
        subtask: SubTask,
        step: int,
        ui_text: str,
        step_dir: Path,
        nodes: list[Any] | None = None,
        term_status_text: str = "",
        incoming_request: RuntimeInformationRequest | RuntimeOperationRequest | None = None,
    ) -> AgentAction:
        last_error: str | None = None
        blocked_action_keys: list[tuple[str, str | None, str | None, str | None]] = []
        for attempt in range(4):
            system = self.system_prompt()
            blocked_action_text = self._blocked_action_prompt(blocked_action_keys, nodes or [])
            user = self.user_prompt(
                subtask=subtask,
                step=step,
                ui_text=ui_text,
                term_status_text=term_status_text,
                incoming_request=incoming_request,
                blocked_action_text=blocked_action_text,
            )
            if last_error:
                user += f"\nPrevious JSON error: {last_error}"
            prompt_path = step_dir / f"llm_prompt_attempt_{attempt + 1}.json"
            try:
                prompt_path.write_text(
                    json.dumps(
                        {"system": system, "user": user},
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                raw_content = self.llm.raw_chat(system=system, user=user)
                response_path = step_dir / f"llm_response_attempt_{attempt + 1}.txt"
                response_path.write_text(raw_content, encoding="utf-8", errors="replace")
                self.reporter.event(
                    "model_call",
                    agent=self.name,
                    step=step,
                    attempt=attempt + 1,
                    prompt=str(prompt_path),
                    response=str(response_path),
                    raw_response=raw_content,
                )
                result = self.llm.parse_json_content(raw_content)
                action = AgentAction.from_json(result)
                action_key = self._action_key(action, nodes or [])
                if action.action == "click":
                    node = find_node(nodes or [], target_id=action.target_id, target_text=action.target_text)
                    if node and node.editable:
                        raise ActionError(
                            "click target is an editable text field; use input with the intended text instead of clicking to focus it"
                        )
                if action.action == "input":
                    node = find_node(nodes or [], target_id=action.target_id, target_text=action.target_text)
                    if node and action.text and normalized_match_text(node.text or node.content_desc) == normalized_match_text(action.text):
                        raise ActionError(
                            "input target already contains the requested text; choose a confirmation control or a different missing field"
                        )
                if action.action in {"click", "input", "swipe", "back"} and action_key in self._recent_action_keys[-4:]:
                    if action_key not in blocked_action_keys:
                        blocked_action_keys.append(action_key)
                    raise ActionError("same primitive action was just tried and did not complete the task; choose a different visible UI node or a final confirmation control if appropriate")
                if action.action in {"click", "input", "swipe", "back"} and action_key in blocked_action_keys:
                    raise ActionError("this primitive action is blocked for this decision because it already failed to make progress")
                if action.action == "REQUEST_INFORMATION" and self.received_information:
                    raise ActionError("information has already been received; use it in the current app instead of requesting again")
                if action.action == "REQUEST_INFORMATION" and action.to_agent == self.name:
                    raise ActionError("do not request information from yourself; if the needed information is visible in this app, finish the provider subtask")
                if action.action == "REQUEST_OPERATION" and action.to_agent == self.name:
                    raise ActionError("do not request an operation from yourself; perform the visible primitive UI action or finish the provider subtask")
                if action.action == "RESPOND_INFORMATION" and not isinstance(incoming_request, RuntimeInformationRequest):
                    raise ActionError("RESPOND_INFORMATION is only valid while answering an incoming information request")
                if action.action == "RESPOND_OPERATION" and not isinstance(incoming_request, RuntimeOperationRequest):
                    raise ActionError("RESPOND_OPERATION is only valid while answering an incoming operation request")
                return action
            except (LlmError, ActionError) as exc:
                last_error = str(exc)
                self.reporter.event(
                    "model_retry",
                    agent=self.name,
                    step=step,
                    attempt=attempt + 1,
                    message=last_error,
                    blocked_actions=[self._format_action_key(key) for key in blocked_action_keys],
                    visible_final_controls=self._visible_confirmation_node_summaries(nodes or []),
                    prompt=str(prompt_path),
                )
        raise ActionError(f"failed to obtain valid action after retry: {last_error}")

    def _action_key(self, action: AgentAction, nodes: list[Any]) -> tuple[str, str | None, str | None, str | None]:
        target = action.target_text
        if target is None and action.target_id is not None:
            node = find_node(nodes, target_id=action.target_id)
            if node:
                target = node.label
            else:
                target = f"id:{action.target_id}"
        return (action.action, target, action.text, action.direction)

    def _format_action_key(self, key: tuple[str, str | None, str | None, str | None]) -> str:
        action, target, text, direction = key
        parts = [f"action={action}"]
        if target:
            parts.append(f"target={target}")
        if text:
            parts.append(f"text={text}")
        if direction:
            parts.append(f"direction={direction}")
        return ", ".join(parts)

    def _visible_confirmation_node_summaries(self, nodes: list[Any]) -> list[str]:
        final_labels = {"save", "done", "ok", "create", "submit", "confirm", "authorize", "pay", "place order", "set alarm"}
        summaries: list[str] = []
        for node in nodes:
            label = (node.text or node.content_desc or "").strip()
            if not label:
                continue
            if node.enabled and (node.clickable or node.content_desc) and label.lower() in final_labels:
                summaries.append(f"id={node.index}, label={label}")
        return summaries

    def _blocked_action_prompt(
        self,
        blocked_action_keys: list[tuple[str, str | None, str | None, str | None]],
        nodes: list[Any],
    ) -> str:
        if not blocked_action_keys:
            return ""
        lines = ["Runtime action mask for this decision:"]
        lines.append("- Do not repeat these primitive actions; they were already tried and did not make progress:")
        lines.extend(f"  * {self._format_action_key(key)}" for key in blocked_action_keys)
        confirmations = self._visible_confirmation_node_summaries(nodes)
        if confirmations:
            lines.append("- Visible final/sub-dialog confirmation controls you may choose if the current values are correct:")
            lines.extend(f"  * {item}" for item in confirmations)
        lines.append("- Choose a different primitive action from the visible UI nodes, or FINISH only if completion is already visible.")
        return "\n".join(lines) + "\n"

    def verify_completion(self, subtask: SubTask, nodes: list[Any]) -> tuple[bool, str]:
        return verify_completion_state(
            instruction=subtask.instruction,
            required_terms=subtask.required_terms,
            forbidden_terms=subtask.forbidden_terms,
            nodes=nodes,
            received_information=self.received_information,
            received_operations=self.received_operations,
            foreground_package=self.adb.foreground_package(),
            expected_package=self.package_name,
        )

    def _memory_path(self) -> Path:
        return MEMORY_STORE.path_for(self.config.name)

    def _load_long_term_memory(self) -> list[str]:
        return MEMORY_STORE.load(self.config.name)

    def remember_lesson(self, lesson: str) -> None:
        lesson = " ".join(lesson.strip().split())
        if not lesson:
            return
        if lesson in self.long_term_memory:
            return
        self.long_term_memory.append(lesson)
        self.long_term_memory = self.long_term_memory[-20:]
        path = MEMORY_STORE.save(self.config.name, self.long_term_memory)
        self.reporter.event("long_term_memory_update", agent=self.name, lesson=lesson, memory_path=str(path))

    def _salient_received_information_terms(self) -> list[str]:
        return salient_received_information_terms(self.received_information)

    def _requires_final_commit(self, subtask: SubTask) -> bool:
        return requires_final_commit(subtask.instruction)

    def _visible_final_confirmation_controls(self, subtask: SubTask, nodes: list[Any]) -> list[str]:
        return visible_final_confirmation_controls(subtask.instruction, nodes)

    def _requires_runtime_response(self, subtask: SubTask) -> bool:
        return requires_runtime_response(subtask.instruction)

    def term_status_text(self, subtask: SubTask, nodes: list[Any]) -> str:
        return build_term_status_text(
            required_terms=subtask.required_terms,
            nodes=nodes,
            received_information=self.received_information,
        )

    def execute_action(self, action: AgentAction, nodes: list[Any]) -> str:
        if action.action == "FINISH":
            return "finished"
        if action.action == "REQUEST_INFORMATION":
            return "waiting"
        if action.action == "REQUEST_OPERATION":
            return "waiting_operation"
        if action.action == "RESPOND_INFORMATION":
            return "responded"
        if action.action == "RESPOND_OPERATION":
            return "responded_operation"
        if action.action == "back":
            self.adb.back()
            return "back"
        if action.action == "swipe":
            self.adb.swipe(action.direction or "up")
            return f"swipe:{action.direction}"
        if action.action == "click":
            node = find_node(nodes, target_id=action.target_id, target_text=action.target_text)
            if not node:
                raise AdbError(f"click target not found: {action.to_json()}")
            x, y = getattr(node, "action_center", None) or node.bounds.center
            self.adb.tap(x, y)
            return f"tap:{x},{y}:{node.label}"
        if action.action == "input":
            if action.target_text or action.target_id is not None:
                node = find_node(nodes, target_id=action.target_id, target_text=action.target_text)
            else:
                node = find_node(nodes, editable_only=True)
            if not node:
                raise AdbError(f"input target not found: {action.to_json()}")
            x, y = getattr(node, "action_center", None) or node.bounds.center
            self.adb.tap(x, y)
            self.adb.settle(0.4)
            self.adb.replace_text(action.text or "")
            return f"input:{node.label}"
        raise ActionError(f"unhandled action: {action.action}")

    def _record_recent_action(self, action: AgentAction, nodes: list[Any]) -> None:
        if action.action not in {"click", "input", "swipe", "back"}:
            return
        self._recent_action_keys.append(self._action_key(action, nodes))
        self._recent_action_keys = self._recent_action_keys[-6:]

    def begin_task(self, subtask: SubTask, out_dir: Path) -> None:
        self._step_subtask = subtask
        self._step_out_dir = out_dir
        self._step_index = 1
        self._step_launched = False
        self._step_done = False
        self._recent_action_keys = []
        self._agentos_nodes_by_snapshot = {}
        self._agentos_step_dir_by_snapshot = {}
        self._agentos_latest_snapshot_id = None
        self.reporter.event("agent_start", agent=self.name, message=subtask.instruction)
        self.reporter.state_event(self.name, "READY", task=subtask.instruction)

    def display_package(self) -> str:
        if self.package_name is None:
            self.package_name = self.adb.pick_package(self.config.package_candidates)
        return self.package_name

    def activate_display_session(self, display_id: int) -> bool:
        if self._step_subtask is None:
            raise RuntimeError(f"{self.name} has no active task. Call begin_task first.")
        if self.package_name is None:
            self.package_name = self.adb.pick_package(self.config.package_candidates)
        if not self._step_launched:
            if display_id == 0:
                self.launch(self._step_subtask)
            else:
                self.adb.force_stop(self.package_name)
                self.adb.launch_package_on_display(self.package_name, display_id)
                self.adb.settle(2.0)
                actual_displays = self.adb.package_display_ids().get(self.package_name, [])
                self.reporter.event(
                    "app_launch_on_display",
                    agent=self.name,
                    package=self.package_name,
                    requested_display_id=display_id,
                    actual_display_ids=actual_displays,
                    matched=display_id in actual_displays,
                )
            self._step_launched = True
            return True
        if display_id == 0:
            foreground = self.adb.foreground_package()
            if foreground != self.package_name:
                self.reporter.event("app_resume", agent=self.name, package=self.package_name, foreground=foreground, method="launcher")
                self.adb.launch_package(self.package_name)
                self.adb.settle(1.0)
                return True
            return False
        self.adb.launch_package_on_display(self.package_name, display_id)
        self.adb.settle(1.0)
        return True

    def observe_display(self, display_id: int) -> ObservationSnapshot:
        if self._step_subtask is None or self._step_out_dir is None:
            raise RuntimeError(f"{self.name} has no active task. Call begin_task first.")
        if self.package_name is None:
            self.package_name = self.adb.pick_package(self.config.package_candidates)
        step_dir = self._step_out_dir / self.name / f"agentos_step_{self._step_index:02d}" / f"display_{display_id}"
        step_dir.mkdir(parents=True, exist_ok=True)
        self.adb.tap_display(display_id, 8, 8)
        self.adb.settle(0.8)
        xml_path = self.adb.dump_ui(step_dir / "window_dump.xml")
        nodes = parse_ui_xml(xml_path)
        observed_packages = sorted({getattr(node, "package", "") for node in nodes if getattr(node, "package", "")})
        observed_package = observed_packages[0] if len(observed_packages) == 1 else self.package_name
        screenshot_path = self.adb.screenshot(step_dir / "screenshot.png")
        ui_text = prompt_snapshot(nodes)
        (step_dir / "visible_texts.json").write_text(
            json.dumps(visible_texts(nodes), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        snapshot = ObservationSnapshot.create(
            agent=self.name,
            display_id=display_id,
            app_package=observed_package or self.display_package(),
            visible_text=ui_text,
            target_nodes=[node.to_prompt_dict() for node in nodes],
            screenshot_path=screenshot_path,
            xml_path=xml_path,
        )
        self._agentos_nodes_by_snapshot[snapshot.snapshot_id] = nodes
        self._agentos_step_dir_by_snapshot[snapshot.snapshot_id] = step_dir
        self._agentos_latest_snapshot_id = snapshot.snapshot_id
        self.reporter.event(
            "display_observe",
            agent=self.name,
            display_id=display_id,
            expected_package=self.package_name,
            observed_packages=observed_packages,
            matched=self.package_name in observed_packages,
            xml=str(xml_path),
            screenshot=str(screenshot_path),
            visible_texts=visible_texts(nodes, limit=120),
        )
        return snapshot

    def decide_from_snapshot(self, snapshot: ObservationSnapshot, subtask: SubTask, out_dir: Path) -> AgentAction:
        nodes = self._agentos_nodes_by_snapshot.get(snapshot.snapshot_id, [])
        step_dir = self._agentos_step_dir_by_snapshot.get(snapshot.snapshot_id, out_dir / self.name / f"agentos_step_{self._step_index:02d}")
        return self.decide_action(
            subtask=subtask,
            step=self._step_index,
            ui_text=snapshot.visible_text,
            step_dir=step_dir,
            nodes=nodes,
            term_status_text=self.term_status_text(subtask, nodes),
        )

    def answer_information_from_snapshot(
        self,
        request: RuntimeInformationRequest,
        snapshot: ObservationSnapshot,
        out_dir: Path,
    ) -> RuntimeInformationResponse:
        nodes = self._agentos_nodes_by_snapshot.get(snapshot.snapshot_id, [])
        step_dir = self._agentos_step_dir_by_snapshot.get(snapshot.snapshot_id, out_dir / self.name / request.request_id)
        subtask = SubTask(
            agent_name=self.config.name,
            instruction=(
                "Answer the incoming RuntimeInformationRequest using the currently visible UI evidence from this app. "
                "Do not navigate or manipulate the UI. Return RESPOND_INFORMATION now."
            ),
            max_steps=1,
        )
        last_error = ""
        for attempt in range(3):
            prompt_dir = step_dir / "response_synthesis"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            system = self.system_prompt() + " For this response-synthesis step, the only valid action is RESPOND_INFORMATION."
            user = self.user_prompt(
                subtask=subtask,
                step=1,
                ui_text=snapshot.visible_text,
                incoming_request=request,
            )
            user += "\nReturn RESPOND_INFORMATION only. Use visible UI evidence; do not infer unsupported facts."
            if last_error:
                user += f"\nPrevious error: {last_error}"
            prompt_path = prompt_dir / f"llm_prompt_attempt_{attempt + 1}.json"
            response_path = prompt_dir / f"llm_response_attempt_{attempt + 1}.txt"
            prompt_path.write_text(json.dumps({"system": system, "user": user}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            try:
                raw_content = self.llm.raw_chat(system=system, user=user)
                response_path.write_text(raw_content, encoding="utf-8", errors="replace")
                self.reporter.event(
                    "model_call",
                    agent=self.name,
                    step="response_synthesis",
                    attempt=attempt + 1,
                    prompt=str(prompt_path),
                    response=str(response_path),
                    raw_response=raw_content,
                )
                action = AgentAction.from_json(self.llm.parse_json_content(raw_content))
                if action.action != "RESPOND_INFORMATION":
                    raise ActionError("planner-declared flow response synthesis must return RESPOND_INFORMATION")
                response = RuntimeInformationResponse(
                    request_id=request.request_id,
                    from_agent=self.name,
                    to_agent=request.from_agent,
                    status="success" if action.status == "success" else "failed",
                    information=action.information or "",
                    source_app=self.config.label,
                    confidence=action.confidence or "low",
                    evidence=action.evidence or "",
                    limitations=action.limitations or "",
                )
                self.reporter.event(
                    "runtime_response_created",
                    response=response,
                    visible_texts=visible_texts(nodes, limit=120),
                    synthesis="provider_agent_from_snapshot",
                )
                return response
            except (LlmError, ActionError) as exc:
                last_error = str(exc)
                self.reporter.event(
                    "model_retry",
                    agent=self.name,
                    step="response_synthesis",
                    attempt=attempt + 1,
                    message=last_error,
                    prompt=str(prompt_path),
                )
        response = RuntimeInformationResponse(
            request_id=request.request_id,
            from_agent=self.name,
            to_agent=request.from_agent,
            status="failed",
            information="",
            source_app=self.config.label,
            confidence="low",
            evidence=snapshot.visible_text[:500],
            limitations=f"No valid RESPOND_INFORMATION was produced: {last_error}",
        )
        self.reporter.event("runtime_response_created", response=response, synthesis="provider_agent_from_snapshot")
        return response

    def apply_display_action(self, display_id: int, action: AgentAction) -> ActionResult:
        if self._step_subtask is None or self._step_out_dir is None:
            raise RuntimeError(f"{self.name} has no active task. Call begin_task first.")
        snapshot = self._agentos_latest_snapshot_id or next(reversed(self._agentos_nodes_by_snapshot), "")
        nodes = self._agentos_nodes_by_snapshot.get(snapshot, [])
        final_commit_action = is_final_confirmation_action(action, nodes)
        status = self.execute_display_action(display_id, action, nodes)
        self._record_recent_action(action, nodes)
        self._step_index += 1
        if action.action == "FINISH":
            verified, message = self.verify_completion(self._step_subtask, nodes)
            if verified:
                self._step_done = True
                return ActionResult(status="finished", message=message)
            self.session_memory.append(f"FINISH was rejected by completion check: {message}. Continue with a primitive UI action.")
            return ActionResult(status="ready", message=message)
        if status in {"waiting", "waiting_operation", "responded", "responded_operation"}:
            return ActionResult(status=status, message=status)
        self.adb.settle(1.2)
        step_dir = self._step_out_dir / self.name / f"agentos_step_{self._step_index:02d}" / f"display_{display_id}"
        auto_finished, message = self._auto_finish_if_complete(
            self._step_subtask,
            step_dir,
            self._step_index,
            final_commit_action=final_commit_action,
        )
        if auto_finished:
            self._step_done = True
            return ActionResult(status="finished", message=message)
        self.session_memory.append(
            f"Previous action {action.action} on this UI did not complete the task: {message}. "
            f"{self.term_status_text(self._step_subtask, nodes).strip()} Choose a different visible control if the UI did not change."
        )
        return ActionResult(status="ready", message=status)

    def execute_display_action(self, display_id: int, action: AgentAction, nodes: list[Any]) -> str:
        if action.action == "FINISH":
            return "finished"
        if action.action == "REQUEST_INFORMATION":
            return "waiting"
        if action.action == "REQUEST_OPERATION":
            return "waiting_operation"
        if action.action == "RESPOND_INFORMATION":
            return "responded"
        if action.action == "RESPOND_OPERATION":
            return "responded_operation"
        if action.action == "back":
            self.adb.back_display(display_id)
            return "back"
        if action.action == "swipe":
            self.adb.swipe_display(display_id, action.direction or "up")
            return f"swipe:{action.direction}"
        if action.action == "click":
            node = find_node(nodes, target_id=action.target_id, target_text=action.target_text)
            if not node:
                raise AdbError(f"click target not found: {action.to_json()}")
            raw_x, raw_y = getattr(node, "action_center", None) or node.bounds.center
            x, y = self._map_node_point_to_display(display_id, raw_x, raw_y, nodes)
            self.adb.tap_display(display_id, x, y)
            return f"tap:{display_id}:{x},{y}:{node.label}"
        if action.action == "input":
            if action.target_text or action.target_id is not None:
                node = find_node(nodes, target_id=action.target_id, target_text=action.target_text)
            else:
                node = find_node(nodes, editable_only=True)
            if not node:
                raise AdbError(f"input target not found: {action.to_json()}")
            raw_x, raw_y = getattr(node, "action_center", None) or node.bounds.center
            x, y = self._map_node_point_to_display(display_id, raw_x, raw_y, nodes)
            self.adb.tap_display(display_id, x, y)
            self.adb.settle(0.4)
            self.adb.input_text_display(display_id, action.text or "")
            return f"input:{display_id}:{node.label}"
        raise ActionError(f"unhandled action: {action.action}")

    def _map_node_point_to_display(self, display_id: int, x: int, y: int, nodes: list[Any]) -> tuple[int, int]:
        if display_id == 0:
            return x, y
        display_size = self.adb.display_size(display_id)
        if not display_size:
            return x, y
        source_width, source_height = self._node_coordinate_space(nodes)
        target_width, target_height = display_size
        if source_width <= 0 or source_height <= 0:
            return x, y
        mapped_x = round(x * target_width / source_width)
        mapped_y = round(y * target_height / source_height)
        mapped_x = max(0, min(target_width - 1, mapped_x))
        mapped_y = max(0, min(target_height - 1, mapped_y))
        if (mapped_x, mapped_y) != (x, y):
            self.reporter.event(
                "display_coordinate_mapped",
                agent=self.name,
                display_id=display_id,
                source_size=[source_width, source_height],
                target_size=[target_width, target_height],
                raw=[x, y],
                mapped=[mapped_x, mapped_y],
            )
        return mapped_x, mapped_y

    def _node_coordinate_space(self, nodes: list[Any]) -> tuple[int, int]:
        max_right = max((getattr(getattr(node, "bounds", None), "right", 0) for node in nodes), default=0)
        max_bottom = max((getattr(getattr(node, "bounds", None), "bottom", 0) for node in nodes), default=0)
        return max_right, max_bottom

    def step_task(self) -> AgentStepResult:
        if self._step_done:
            return AgentStepResult(status="finished")
        if self._step_subtask is None or self._step_out_dir is None:
            raise RuntimeError(f"{self.name} has no active task. Call begin_task first.")
        subtask = self._step_subtask
        if not self._step_launched:
            self.reporter.state_event(self.name, "RUNNING", step=0, action="launch")
            self.launch(subtask)
            self._step_launched = True
            self.reporter.state_event(self.name, "READY", step=0, action="launch_complete")
            return AgentStepResult(status="ready", message="launched")
        if self._step_index > subtask.max_steps:
            self._step_done = True
            self.reporter.event("agent_finish", agent=self.name, message="max steps reached")
            self.reporter.state_event(self.name, "FAILED", message="max steps reached")
            self.remember_lesson(f"When doing '{subtask.instruction[:120]}', avoid repeating ineffective actions; max steps were reached.")
            return AgentStepResult(status="failed", message="max steps reached")

        step = self._step_index
        step_dir = self._step_out_dir / self.name / f"step_{step:02d}"
        self.reporter.state_event(self.name, "RUNNING", step=step)
        try:
            xml_path, screenshot_path, nodes = self._observe_step(step_dir, subtask)
        except Exception as exc:
                self._step_done = True
                self.reporter.event("agent_step", agent=self.name, step=step, action=None, status="failed", reason=str(exc))
                self.reporter.event("agent_finish", agent=self.name, message=f"failed: {exc}")
                self.reporter.state_event(self.name, "FAILED", step=step, message=str(exc))
                self.remember_lesson(f"Observation failed during {self.config.label}: {exc}")
                return AgentStepResult(status="failed", message=str(exc))
        ui_text = prompt_snapshot(nodes)
        (step_dir / "visible_texts.json").write_text(
            json.dumps(visible_texts(nodes), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            action = self.decide_action(
                subtask=subtask,
                step=step,
                ui_text=ui_text,
                step_dir=step_dir,
                nodes=nodes,
                term_status_text=self.term_status_text(subtask, nodes),
            )
            final_commit_action = is_final_confirmation_action(action, nodes)
            status = self.execute_action(action, nodes)
            self._record_recent_action(action, nodes)
            self.reporter.event(
                "agent_step",
                agent=self.name,
                step=step,
                action=action.to_json(),
                status=status,
                reason=action.reason,
                xml=str(xml_path),
                screenshot=str(screenshot_path),
                visible_texts=visible_texts(nodes, limit=120),
            )
            self._step_index += 1
            if action.action == "REQUEST_INFORMATION":
                request = RuntimeInformationRequest.create(
                    from_agent=self.name,
                    to_agent=action.to_agent or "",
                    need=action.need or "",
                    context=action.context or "",
                    purpose=action.purpose or "",
                    resume_instruction=action.resume_instruction or "",
                )
                self.reporter.event(
                    "agent_paused",
                    agent=self.name,
                    step=step,
                    request=request,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                )
                self.reporter.event("runtime_request_created", request=request)
                self.session_memory.append(f"Paused for runtime request {request.request_id}: {request.need}")
                self.reporter.state_event(self.name, "WAIT_PEER", step=step, request_id=request.request_id)
                return AgentStepResult(status="waiting", action=action, request=request, message=action.reason)
            if action.action == "REQUEST_OPERATION":
                request = RuntimeOperationRequest.create(
                    from_agent=self.name,
                    to_agent=action.to_agent or "",
                    operation=action.operation or "",
                    context=action.context or "",
                    purpose=action.purpose or "",
                    expected_result=action.expected_result or "",
                    resume_instruction=action.resume_instruction or "",
                )
                self.reporter.event(
                    "agent_paused",
                    agent=self.name,
                    step=step,
                    request=request,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                )
                self.reporter.event("runtime_operation_request_created", request=request)
                self.session_memory.append(f"Paused for runtime operation {request.request_id}: {request.operation}")
                self.reporter.state_event(self.name, "WAIT_PEER", step=step, request_id=request.request_id)
                return AgentStepResult(status="waiting_operation", action=action, request=request, message=action.reason)
            if action.action == "FINISH":
                verified, verify_message = self.verify_completion(subtask, nodes)
                self.reporter.event(
                    "completion_check",
                    agent=self.name,
                    step=step,
                    verified=verified,
                    message=verify_message,
                    required_terms=list(subtask.required_terms),
                    forbidden_terms=list(subtask.forbidden_terms),
                )
                self._step_done = True
                if not verified:
                    self._step_done = False
                    self.session_memory.append(f"FINISH was rejected by completion check: {verify_message}. Continue with a primitive UI action.")
                    self.remember_lesson(
                        f"When doing '{subtask.instruction[:120]}', do not finish until completion check passes: {verify_message}"
                    )
                    self.reporter.state_event(self.name, "READY", step=step, message=verify_message)
                    return AgentStepResult(status="ready", action=action, message=verify_message)
                self.reporter.event("agent_finish", agent=self.name, message="finished")
                self.reporter.state_event(self.name, "DONE", step=step)
                return AgentStepResult(status="finished", action=action)
            self.adb.settle(1.2)
            auto_finished, verify_message = self._auto_finish_if_complete(
                subtask,
                step_dir,
                step,
                final_commit_action=final_commit_action,
            )
            if auto_finished:
                self._step_done = True
                self.reporter.event("agent_finish", agent=self.name, message="finished after post-action verification")
                self.reporter.state_event(self.name, "DONE", step=step, action="post_action_verification")
                return AgentStepResult(status="finished", action=action, message=verify_message)
            self.session_memory.append(
                f"Previous action {action.action} did not complete the task: {verify_message}. "
                f"{self.term_status_text(subtask, nodes).strip()} Choose a different primitive action if the UI did not change."
            )
            self.reporter.state_event(self.name, "READY", step=step, action=action.action)
            return AgentStepResult(status="ready", action=action)
        except Exception as exc:
            self._step_done = True
            self.reporter.event(
                "agent_step",
                agent=self.name,
                step=step,
                action=None,
                status="failed",
                reason=str(exc),
                xml=str(xml_path),
                screenshot=str(screenshot_path),
                visible_texts=visible_texts(nodes, limit=120),
            )
            self.reporter.event("agent_finish", agent=self.name, message=f"failed: {exc}")
            self.reporter.state_event(self.name, "FAILED", step=step, message=str(exc))
            self.remember_lesson(f"Action failed in {self.config.label}: {exc}")
            return AgentStepResult(status="failed", message=str(exc))

    def _auto_finish_if_complete(self, subtask: SubTask, step_dir: Path, step: int, *, final_commit_action: bool) -> tuple[bool, str]:
        try:
            xml_path = self.adb.dump_ui(step_dir / "post_action_window_dump.xml")
            nodes = parse_ui_xml(xml_path)
        except Exception as exc:
            self.reporter.event("post_action_completion_check", agent=self.name, step=step, verified=False, message=f"observation failed: {exc}")
            return False, str(exc)
        verified, verify_message = self.verify_completion(subtask, nodes)
        if verified and requires_final_commit(subtask.instruction) and not final_commit_action:
            verified = False
            verify_message = "final commit action required before auto completion"
        self.reporter.event(
            "post_action_completion_check",
            agent=self.name,
            step=step,
            verified=verified,
            message=verify_message,
            required_terms=list(subtask.required_terms),
            forbidden_terms=list(subtask.forbidden_terms),
            xml=str(xml_path),
            visible_texts=visible_texts(nodes, limit=120),
        )
        return verified, verify_message

    def _observe_step(self, step_dir: Path, subtask: SubTask, *, activate: bool = True) -> tuple[Path, Path, list[Any]]:
        if activate:
            self.activate(subtask)
        try:
            xml_path = self.adb.dump_ui(step_dir / "window_dump.xml")
        except AdbError as exc:
            self.reporter.event("ui_observe_retry", agent=self.name, reason=str(exc), method="cold_resume")
            if self.package_name:
                self.adb.force_stop(self.package_name)
            self._launch_once(subtask)
            self.adb.settle(3.0)
            xml_path = self.adb.dump_ui(step_dir / "window_dump_after_retry.xml")
        screenshot_path = self.adb.screenshot(step_dir / "screenshot.png")
        nodes = parse_ui_xml(xml_path)
        return xml_path, screenshot_path, nodes

    def _record_serial_switch(self, *, switched: bool, started: float, purpose: str, step: int, action: str | None = None) -> None:
        if not switched:
            return
        elapsed = round(time.monotonic() - started, 3)
        switch_t = round(time.monotonic() - self.reporter.started_monotonic - elapsed, 3)
        self.reporter.state_event(self.name, "SWITCH", t=switch_t, runtime="steward_serial", display_id=0, purpose=purpose, step=step, action=action)
        self.reporter.event("display_switch", runtime="steward_serial", agent=self.name, display_id=0, purpose=purpose, step=step, action=action, elapsed=elapsed)

    def run(self, subtask: SubTask, out_dir: Path) -> AgentRunResult:
        self.reporter.event("agent_start", agent=self.name, message=subtask.instruction)
        self._recent_action_keys = []
        self.reporter.state_event(self.name, "READY", runtime="steward_serial", task=subtask.instruction)
        started = time.monotonic()
        self.launch(subtask)
        self._record_serial_switch(switched=True, started=started, purpose="launch", step=0)
        for step in range(1, subtask.max_steps + 1):
            step_dir = out_dir / self.name / f"step_{step:02d}"
            try:
                started = time.monotonic()
                switched = self.activate(subtask)
                self._record_serial_switch(switched=switched, started=started, purpose="observe", step=step)
                self.reporter.state_event(self.name, "OBSERVING", runtime="steward_serial", step=step, display_id=0)
                xml_path, screenshot_path, nodes = self._observe_step(step_dir, subtask, activate=False)
            except Exception as exc:
                self.reporter.event("agent_step", agent=self.name, step=step, action=None, status="failed", reason=str(exc))
                self.reporter.event("agent_finish", agent=self.name, message=f"failed: {exc}")
                self.reporter.state_event(self.name, "FAILED", runtime="steward_serial", step=step, message=str(exc))
                return AgentRunResult(status="failed", message=str(exc))
            ui_text = prompt_snapshot(nodes)
            (step_dir / "visible_texts.json").write_text(
                json.dumps(visible_texts(nodes), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            try:
                self.reporter.state_event(self.name, "THINKING", runtime="steward_serial", step=step, display_id=0)
                action = self.decide_action(
                    subtask=subtask,
                    step=step,
                    ui_text=ui_text,
                    step_dir=step_dir,
                    nodes=nodes,
                    term_status_text=self.term_status_text(subtask, nodes),
                )
                final_commit_action = is_final_confirmation_action(action, nodes)
                started = time.monotonic()
                switched = self.activate(subtask)
                self._record_serial_switch(switched=switched, started=started, purpose="act", step=step, action=action.action)
                self.reporter.state_event(self.name, "ACTING", runtime="steward_serial", step=step, display_id=0, action=action.action)
                status = self.execute_action(action, nodes)
                self._record_recent_action(action, nodes)
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=action.to_json(),
                    status=status,
                    reason=action.reason,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                    visible_texts=visible_texts(nodes, limit=120),
                )
                if action.action == "REQUEST_INFORMATION":
                    request = RuntimeInformationRequest.create(
                        from_agent=self.name,
                        to_agent=action.to_agent or "",
                        need=action.need or "",
                        context=action.context or "",
                        purpose=action.purpose or "",
                        resume_instruction=action.resume_instruction or "",
                    )
                    self.reporter.event(
                        "agent_paused",
                        agent=self.name,
                        step=step,
                        request=request,
                        xml=str(xml_path),
                        screenshot=str(screenshot_path),
                    )
                    self.reporter.event("runtime_request_created", request=request)
                    self.reporter.ipc_event(
                        request_id=request.request_id,
                        message_kind="RuntimeInformationRequest",
                        status="created",
                        from_agent=request.from_agent,
                        to_agent=request.to_agent,
                        request_summary=request.need,
                        evidence_ref=str(xml_path),
                    )
                    self.session_memory.append(f"Paused for runtime request {request.request_id}: {request.need}")
                    self.reporter.state_event(self.name, "WAIT_PEER", runtime="steward_serial", step=step, request_id=request.request_id)
                    return AgentRunResult(status="waiting", request=request, message=action.reason)
                if action.action == "REQUEST_OPERATION":
                    request = RuntimeOperationRequest.create(
                        from_agent=self.name,
                        to_agent=action.to_agent or "",
                        operation=action.operation or "",
                        context=action.context or "",
                        purpose=action.purpose or "",
                        expected_result=action.expected_result or "",
                        resume_instruction=action.resume_instruction or "",
                    )
                    self.reporter.event(
                        "agent_paused",
                        agent=self.name,
                        step=step,
                        request=request,
                        xml=str(xml_path),
                        screenshot=str(screenshot_path),
                    )
                    self.reporter.event("runtime_operation_request_created", request=request)
                    self.reporter.ipc_event(
                        request_id=request.request_id,
                        message_kind="RuntimeOperationRequest",
                        status="created",
                        from_agent=request.from_agent,
                        to_agent=request.to_agent,
                        request_summary=request.operation,
                        evidence_ref=str(xml_path),
                        policy_decision="not_checked",
                    )
                    self.session_memory.append(f"Paused for runtime operation {request.request_id}: {request.operation}")
                    self.reporter.state_event(self.name, "WAIT_PEER", runtime="steward_serial", step=step, request_id=request.request_id)
                    return AgentRunResult(status="waiting_operation", request=request, message=action.reason)
                if action.action == "FINISH":
                    verified, verify_message = self.verify_completion(subtask, nodes)
                    self.reporter.event(
                        "completion_check",
                        agent=self.name,
                        step=step,
                        verified=verified,
                        message=verify_message,
                        required_terms=list(subtask.required_terms),
                        forbidden_terms=list(subtask.forbidden_terms),
                    )
                    if not verified:
                        self.session_memory.append(f"FINISH was rejected by completion check: {verify_message}. Continue with a primitive UI action.")
                        self.remember_lesson(
                            f"When doing '{subtask.instruction[:120]}', do not finish until completion check passes: {verify_message}"
                        )
                        self.reporter.state_event(self.name, "READY", runtime="steward_serial", step=step, message=verify_message)
                        continue
                    self.reporter.event("agent_finish", agent=self.name, message="finished")
                    self.reporter.state_event(self.name, "DONE", runtime="steward_serial", step=step)
                    return AgentRunResult(status="finished")
                self.adb.settle(1.2)
                auto_finished, verify_message = self._auto_finish_if_complete(
                    subtask,
                    step_dir,
                    step,
                    final_commit_action=final_commit_action,
                )
                if auto_finished:
                    self.reporter.event("agent_finish", agent=self.name, message="finished after post-action verification")
                    self.reporter.state_event(self.name, "DONE", runtime="steward_serial", step=step, action="post_action_verification")
                    return AgentRunResult(status="finished", message=verify_message)
                self.reporter.state_event(self.name, "READY", runtime="steward_serial", step=step, action=action.action)
            except Exception as exc:
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=None,
                    status="failed",
                    reason=str(exc),
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                    visible_texts=visible_texts(nodes, limit=120),
                )
                self.reporter.event("agent_finish", agent=self.name, message=f"failed: {exc}")
                self.reporter.state_event(self.name, "FAILED", runtime="steward_serial", step=step, message=str(exc))
                self.remember_lesson(f"Action failed in {self.config.label}: {exc}")
                return AgentRunResult(status="failed", message=str(exc))
        self.reporter.event("agent_finish", agent=self.name, message="max steps reached")
        self.reporter.state_event(self.name, "FAILED", runtime="steward_serial", message="max steps reached")
        self.remember_lesson(f"When doing '{subtask.instruction[:120]}', avoid repeating ineffective actions; max steps were reached.")
        return AgentRunResult(status="failed", message="max steps reached")

    def receive_information(self, response: RuntimeInformationResponse) -> None:
        if any(existing.request_id == response.request_id for existing in self.received_information):
            return
        self.received_information.append(response)
        self.session_memory.append(f"Received {response.request_id} from {response.from_agent}: {response.information}")
        self.reporter.event("agent_resumed", agent=self.name, response=response)
        self.reporter.state_event(self.name, "READY", request_id=response.request_id, from_agent=response.from_agent)

    def receive_operation(self, response: RuntimeOperationResponse) -> None:
        if any(existing.request_id == response.request_id for existing in self.received_operations):
            return
        self.received_operations.append(response)
        self.session_memory.append(f"Received operation {response.request_id} from {response.from_agent}: {response.status} {response.result}")
        self.reporter.event("agent_resumed", agent=self.name, response=response)
        self.reporter.state_event(self.name, "READY", request_id=response.request_id, from_agent=response.from_agent)

    def handle_information_request(
        self,
        request: RuntimeInformationRequest,
        out_dir: Path,
        *,
        record_ipc: bool = True,
    ) -> RuntimeInformationResponse:
        self.reporter.event("runtime_request_received", agent=self.name, request=request)
        self.reporter.state_event(self.name, "RUNNING", request_id=request.request_id, action="handle_information_request")
        if record_ipc:
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationRequest",
                status="received",
                from_agent=request.from_agent,
                to_agent=request.to_agent,
                request_summary=request.need,
            )
        subtask = SubTask(
            agent_name=self.config.name,
            instruction=(
                "Answer the incoming RuntimeInformationRequest using this app if possible. "
                "Navigate within your own app as needed. Return RESPOND_INFORMATION when you have enough evidence or when the answer is not available."
            ),
            max_steps=6,
        )
        self.launch(subtask)
        request_dir = out_dir / self.name / request.request_id
        for step in range(1, subtask.max_steps + 1):
            step_dir = request_dir / f"step_{step:02d}"
            try:
                xml_path, screenshot_path, nodes = self._observe_step(step_dir, subtask)
                ui_text = prompt_snapshot(nodes)
                action = self.decide_action(
                    subtask=subtask,
                    step=step,
                    ui_text=ui_text,
                    step_dir=step_dir,
                    nodes=nodes,
                    incoming_request=request,
                )
                status = self.execute_action(action, nodes)
                self._record_recent_action(action, nodes)
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=action.to_json(),
                    status=status,
                    reason=action.reason,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                    visible_texts=visible_texts(nodes, limit=120),
                )
                if action.action == "RESPOND_INFORMATION":
                    response = RuntimeInformationResponse(
                        request_id=request.request_id,
                        from_agent=self.name,
                        to_agent=request.from_agent,
                        status="success" if action.status == "success" else "failed",
                        information=action.information or "",
                        source_app=self.config.label,
                        confidence=action.confidence or "low",
                        evidence=action.evidence or "",
                        limitations=action.limitations or "",
                    )
                    self.session_memory.append(f"Answered {request.request_id}: {response.status} {response.information}")
                    self.reporter.event("runtime_response_created", response=response, visible_texts=visible_texts(nodes, limit=120))
                    if record_ipc:
                        self.reporter.ipc_event(
                            request_id=request.request_id,
                            message_kind="RuntimeInformationResponse",
                            status=response.status,
                            from_agent=response.from_agent,
                            to_agent=response.to_agent,
                            request_summary=request.need,
                            response_summary=response.information,
                            evidence=response.evidence,
                            evidence_ref=str(xml_path),
                        )
                    self.reporter.state_event(self.name, "READY", request_id=request.request_id, action="response_created")
                    return response
                self.adb.settle(1.2)
                self.session_memory.append(
                    f"Previous request-handling action {action.action} was executed. If the UI did not change, choose a different primitive action such as input, swipe, back, or response."
                )
            except Exception as exc:
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=None,
                    status="failed",
                    reason=str(exc),
                )
                break
        response = RuntimeInformationResponse(
            request_id=request.request_id,
            from_agent=self.name,
            to_agent=request.from_agent,
            status="failed",
            information="",
            source_app=self.config.label,
            confidence="low",
            limitations="No valid RESPOND_INFORMATION action was produced within the step limit.",
        )
        self.reporter.event("runtime_response_created", response=response)
        if record_ipc:
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationResponse",
                status="failed",
                from_agent=response.from_agent,
                to_agent=response.to_agent,
                request_summary=request.need,
                response_summary=response.limitations,
            )
        self.reporter.state_event(self.name, "FAILED", request_id=request.request_id, message=response.limitations)
        return response

    def handle_operation_request(
        self,
        request: RuntimeOperationRequest,
        out_dir: Path,
        *,
        record_ipc: bool = True,
    ) -> RuntimeOperationResponse:
        self.reporter.event("runtime_operation_request_received", agent=self.name, request=request)
        self.reporter.state_event(self.name, "RUNNING", request_id=request.request_id, action="handle_operation_request")
        if record_ipc:
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeOperationRequest",
                status="received",
                from_agent=request.from_agent,
                to_agent=request.to_agent,
                request_summary=request.operation,
                policy_decision="not_checked",
            )
        subtask = SubTask(
            agent_name=self.config.name,
            instruction=(
                "Handle the incoming RuntimeOperationRequest using this app if possible. "
                "Perform visible UI actions needed for the operation. Return RESPOND_OPERATION only after the result is visible or unavailable."
            ),
            max_steps=6,
        )
        self.launch(subtask)
        request_dir = out_dir / self.name / request.request_id
        last_reason = "No valid RESPOND_OPERATION action was produced within the step limit."
        for step in range(1, subtask.max_steps + 1):
            step_dir = request_dir / f"step_{step:02d}"
            try:
                xml_path, screenshot_path, nodes = self._observe_step(step_dir, subtask)
                ui_text = prompt_snapshot(nodes)
                action = self.decide_action(
                    subtask=subtask,
                    step=step,
                    ui_text=ui_text,
                    step_dir=step_dir,
                    nodes=nodes,
                    incoming_request=request,
                )
                status = self.execute_action(action, nodes)
                self._record_recent_action(action, nodes)
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=action.to_json(),
                    status=status,
                    reason=action.reason,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                    visible_texts=visible_texts(nodes, limit=120),
                )
                if action.action == "RESPOND_OPERATION":
                    if not self._operation_response_supported(action, nodes):
                        last_reason = "RESPOND_OPERATION rejected because its success evidence is not visible in the current UI."
                        self.session_memory.append(last_reason)
                        self.reporter.event(
                            "operation_response_rejected",
                            agent=self.name,
                            step=step,
                            reason=last_reason,
                            action=action.to_json(),
                            visible_texts=visible_texts(nodes, limit=120),
                        )
                        self.adb.settle(1.2)
                        continue
                    response = RuntimeOperationResponse(
                        request_id=request.request_id,
                        from_agent=self.name,
                        to_agent=request.from_agent,
                        status="success" if action.status == "success" else "failed",
                        result=action.result or "",
                        source_app=self.config.label,
                        evidence=action.evidence or "",
                        limitations=action.limitations or "",
                    )
                    self.session_memory.append(f"Answered operation {request.request_id}: {response.status} {response.result}")
                    self.reporter.event("runtime_operation_response_created", response=response, visible_texts=visible_texts(nodes, limit=120))
                    if record_ipc:
                        self.reporter.ipc_event(
                            request_id=request.request_id,
                            message_kind="RuntimeOperationResponse",
                            status=response.status,
                            from_agent=response.from_agent,
                            to_agent=response.to_agent,
                            request_summary=request.operation,
                            response_summary=response.result or response.limitations,
                            evidence=response.evidence,
                            evidence_ref=str(xml_path),
                            policy_decision="not_checked",
                        )
                    self.reporter.state_event(self.name, "READY", request_id=request.request_id, action="operation_response_created")
                    return response
                self.adb.settle(1.2)
                self.session_memory.append(
                    f"Previous operation-handling action {action.action} was executed. If the UI did not change, choose a different primitive action such as input, swipe, back, or response."
                )
            except Exception as exc:
                last_reason = str(exc)
                self.reporter.event("agent_step", agent=self.name, step=step, action=None, status="failed", reason=last_reason)
                break
        response = RuntimeOperationResponse(
            request_id=request.request_id,
            from_agent=self.name,
            to_agent=request.from_agent,
            status="failed",
            result="",
            source_app=self.config.label,
            limitations=last_reason,
        )
        self.reporter.event("runtime_operation_response_created", response=response)
        if record_ipc:
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeOperationResponse",
                status="failed",
                from_agent=response.from_agent,
                to_agent=response.to_agent,
                request_summary=request.operation,
                response_summary=response.limitations,
                policy_decision="not_checked",
            )
        self.reporter.state_event(self.name, "FAILED", request_id=request.request_id, message=response.limitations)
        return response

    def _operation_response_supported(self, action: AgentAction, nodes: list[Any]) -> bool:
        if action.status != "success":
            return True
        evidence = (action.evidence or "").strip().lower()
        if not evidence:
            return False
        haystack = "\n".join(visible_texts(nodes, limit=120)).lower()
        return evidence in haystack
