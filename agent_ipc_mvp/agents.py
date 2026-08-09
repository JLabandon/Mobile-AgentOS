from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .actions import ActionError, AgentAction
from .adb import AdbClient, AdbError
from .llm import DeepSeekClient, LlmError
from .report import RunReporter
from .runtime_requests import AgentRunResult, RuntimeInformationRequest, RuntimeInformationResponse
from .task_plan import TaskPlan
from .ui_tree import find_node, parse_ui_xml, prompt_snapshot, visible_texts


@dataclass(frozen=True)
class AppConfig:
    name: str
    label: str
    package_candidates: list[str]
    launch: dict[str, Any]
    capabilities: tuple[str, ...] = ()
    description: str = ""
    task_guidelines: tuple[str, ...] = ()
    semantic_slots: dict[str, Any] | None = None


@dataclass(frozen=True)
class SubTask:
    agent_name: str
    instruction: str
    max_steps: int = 6
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    launch_args: tuple[str, ...] = ()


class AppStaffAgent:
    def __init__(self, *, config: AppConfig, adb: AdbClient, llm: DeepSeekClient, reporter: RunReporter) -> None:
        self.config = config
        self.adb = adb
        self.llm = llm
        self.reporter = reporter
        self.package_name: str | None = None
        self.received_information: list[RuntimeInformationResponse] = []
        self.assigned_slots: dict[str, str] = {}
        self.session_memory: list[str] = []
        self.available_peers: list[AppConfig] = []

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
        return (
            "You are an app-oriented mobile StaffAgent. Return json only. "
            "You control exactly one mobile app. Use your app profile, task guideline memory, current UI, "
            "session memory, and IPC messages to decide the next step. "
            "You may choose exactly one action from click, input, swipe, back, REQUEST_INFORMATION, RESPOND_INFORMATION, SET_SLOT, FINISH. "
            "If you cannot continue because another app agent has needed information, choose REQUEST_INFORMATION. "
            "If your assigned task is to answer a runtime request and the current app UI supports an answer, choose RESPOND_INFORMATION. "
            "Use only the UI nodes and memories given by the user. Complete the requested flow. "
            "You may tap Save, Done, OK, Create, or final confirmation controls. "
            "Return FINISH only after the final record is visible or the requested alarm/event is already present. "
            "JSON schema for UI actions: {\"action\":\"click|input|swipe|back|FINISH\","
            "\"target_id\":0,\"target_text\":\"optional\",\"text\":\"optional\","
            "\"direction\":\"up|down|left|right\",\"reason\":\"short\"}. "
            "JSON schema for semantic slot assignment: {\"action\":\"SET_SLOT\","
            "\"slot\":\"one of the app profile semantic slots\",\"text\":\"value\",\"reason\":\"short\"}. "
            "JSON schema for information requests: {\"action\":\"REQUEST_INFORMATION\","
            "\"to_agent\":\"target_agent\",\"need\":\"needed information\",\"context\":\"task context\","
            "\"purpose\":\"why it is needed\",\"resume_instruction\":\"how to continue after response\","
            "\"reason\":\"short\"}. "
            "JSON schema for information responses: {\"action\":\"RESPOND_INFORMATION\","
            "\"status\":\"success|failed\",\"information\":\"short answer or empty on failure\","
            "\"evidence\":\"visible text or memory supporting the answer\","
            "\"confidence\":\"high|medium|low\",\"limitations\":\"optional\",\"reason\":\"short\"}. "
            "Example json output: {\"action\":\"FINISH\",\"reason\":\"requested item is visible\"}."
        )

    def profile_prompt(self) -> str:
        slots = self.config.semantic_slots or {}
        peer_text = "\n".join(
            f"- {peer.name}_agent: {peer.label}; capabilities: {', '.join(peer.capabilities) or 'none'}; description: {peer.description}"
            for peer in self.available_peers
        )
        return (
            f"App profile:\n"
            f"- agent_name: {self.name}\n"
            f"- app_label: {self.config.label}\n"
            f"- app_description: {self.config.description or 'No description provided.'}\n"
            f"- capabilities: {', '.join(self.config.capabilities) or 'none'}\n"
            f"- semantic_slots: {json.dumps(slots, ensure_ascii=False)}\n"
            "Available IPC peers:\n"
            f"{peer_text or '- none'}\n"
            "Task guideline memory:\n"
            + "\n".join(f"- {item}" for item in self.config.task_guidelines)
            + ("\n" if self.config.task_guidelines else "- none\n")
        )

    def user_prompt(self, *, subtask: SubTask, step: int, ui_text: str, incoming_request: RuntimeInformationRequest | None = None) -> str:
        info_text = ""
        if self.received_information:
            info_text = "Received runtime information:\n" + "\n".join(
                f"- {response.information} (from {response.from_agent}; evidence: {response.evidence})" for response in self.received_information
            ) + "\n"
            info_text += (
                "The received information is not complete until it is entered or used in this app. "
                "Do not tap Save/Done/OK/Create or FINISH while required terms from the received information are missing from both the visible UI and assigned semantic slots.\n"
                "Do not request the same information again; continue with the information already received.\n"
                "Choose how to use the information yourself according to your app profile and assigned task.\n"
            )
        slot_text = ""
        if self.assigned_slots:
            slot_text += "Already assigned semantic slots:\n" + "\n".join(
                f"- {slot}: {text}" for slot, text in self.assigned_slots.items()
            ) + "\nDo not assign the same slot again; continue by saving or finishing even if a collapsed UI field does not display the value.\n"
        required_text = ""
        if subtask.required_terms:
            required_text = "Required terms that must be visible before final save/FINISH: " + ", ".join(subtask.required_terms) + "\n"
        forbidden_text = ""
        if subtask.forbidden_terms:
            forbidden_text = "Terms that must not still be visible at FINISH: " + ", ".join(subtask.forbidden_terms) + "\n"
        request_text = ""
        if incoming_request:
            request_text = (
                "Incoming RuntimeInformationRequest:\n"
                f"{json.dumps(incoming_request, default=lambda obj: obj.__dict__, ensure_ascii=False, indent=2)}\n"
                "Your current assigned task is to use your own app and memory to answer this request if possible. "
                "Do not decide the requesting agent's UI actions.\n"
            )
        memory_text = ""
        if self.session_memory:
            memory_text = "Session working memory:\n" + "\n".join(f"- {item}" for item in self.session_memory[-8:]) + "\n"
        return (
            f"{self.profile_prompt()}"
            f"Instruction: {subtask.instruction}\n"
            f"Step: {step}\n"
            f"{request_text}"
            f"{memory_text}"
            f"{info_text}"
            f"{slot_text}"
            f"{required_text}"
            f"{forbidden_text}"
            "Important: complete the requested event/alarm. If a Save/Done/OK/Create button is needed and visible, tap it. "
            "Return FINISH only after completion.\n"
            "Visible UI nodes:\n"
            f"{ui_text}\n"
            "Return one json action object matching the schema exactly."
        )

    def decide_action(
        self,
        *,
        subtask: SubTask,
        step: int,
        ui_text: str,
        step_dir: Path,
        incoming_request: RuntimeInformationRequest | None = None,
    ) -> AgentAction:
        last_error: str | None = None
        for attempt in range(2):
            system = self.system_prompt()
            user = self.user_prompt(
                subtask=subtask,
                step=step,
                ui_text=ui_text,
                incoming_request=incoming_request,
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
                if action.action == "REQUEST_INFORMATION" and self.received_information:
                    raise ActionError("information has already been received; use it in the current app instead of requesting again")
                if action.action == "RESPOND_INFORMATION" and not incoming_request:
                    raise ActionError("RESPOND_INFORMATION is only valid while answering an incoming request")
                if action.action == "SET_SLOT" and action.slot in self.assigned_slots:
                    raise ActionError(f"{action.slot} has already been assigned; save or finish instead of assigning it again")
                return action
            except (LlmError, ActionError) as exc:
                last_error = str(exc)
                self.reporter.event(
                    "model_retry",
                    agent=self.name,
                    step=step,
                    attempt=attempt + 1,
                    message=last_error,
                    prompt=str(prompt_path),
                )
        raise ActionError(f"failed to obtain valid action after retry: {last_error}")

    def verify_completion(self, subtask: SubTask, nodes: list[Any]) -> tuple[bool, str]:
        texts = visible_texts(nodes, limit=120)
        assigned_texts = list(self.assigned_slots.values())
        haystack = "\n".join([*texts, *assigned_texts]).lower()
        normalized_texts = {text.strip().lower() for text in texts}
        missing = [term for term in subtask.required_terms if term.lower() not in haystack]
        present_forbidden = [term for term in subtask.forbidden_terms if term.strip().lower() in normalized_texts]
        foreground = self.adb.foreground_package()
        wrong_foreground = foreground != self.package_name
        if missing or present_forbidden:
            parts = []
            if missing:
                parts.append(f"missing required terms: {missing}")
            if present_forbidden:
                parts.append(f"forbidden terms still visible: {present_forbidden}")
            if wrong_foreground:
                parts.append(f"wrong foreground package: expected={self.package_name}, foreground={foreground}")
            return False, "; ".join(parts)
        if wrong_foreground:
            return False, f"wrong foreground package: expected={self.package_name}, foreground={foreground}"
        return True, "completion terms verified"

    def execute_action(self, action: AgentAction, nodes: list[Any]) -> str:
        if action.action == "FINISH":
            return "finished"
        if action.action == "REQUEST_INFORMATION":
            return "waiting"
        if action.action == "RESPOND_INFORMATION":
            return "responded"
        if action.action == "SET_SLOT":
            return self.set_slot(action, nodes)
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
            x, y = node.bounds.center
            self.adb.tap(x, y)
            return f"tap:{x},{y}:{node.label}"
        if action.action == "input":
            node = find_node(
                nodes,
                target_id=action.target_id,
                target_text=action.target_text,
                editable_only=bool(action.target_text or action.target_id is not None),
            ) or find_node(nodes, editable_only=True)
            if not node:
                raise AdbError(f"input target not found: {action.to_json()}")
            x, y = node.bounds.center
            self.adb.tap(x, y)
            self.adb.settle(0.4)
            self.adb.input_text(action.text or "")
            return f"input:{node.label}"
        raise ActionError(f"unhandled action: {action.action}")

    def set_slot(self, action: AgentAction, nodes: list[Any]) -> str:
        slots = self.config.semantic_slots or {}
        slot_config = slots.get(action.slot or "")
        if not slot_config:
            raise ActionError(f"{self.name} does not support semantic slot assignment: {action.slot}")
        if slot_config.get("type") == "intent_extra":
            return self._set_slot_with_intent(action, slot_config)
        if slot_config.get("type") == "ui_search_suggestion":
            return self._set_slot_with_ui_search_suggestion(action, nodes, slot_config)
        raise ActionError(f"unsupported semantic slot adapter type: {slot_config.get('type')}")

    def _set_slot_with_intent(self, action: AgentAction, slot_config: dict[str, Any]) -> str:
        if self.package_name is None:
            raise AdbError(f"{self.name} package is not initialized")
        launch = self.config.launch
        if launch.get("mode") != "intent":
            raise ActionError("intent_extra slot assignment requires an intent launch config")
        extra_type = str(slot_config.get("extra_type", "--es"))
        extra_name = str(slot_config.get("extra_name", ""))
        if not extra_name:
            raise ActionError(f"intent_extra slot requires extra_name: {action.slot}")
        args = [str(arg) for arg in launch.get("args", [])]
        if len(args) >= 2 and args[0] == "am" and args[1] == "start" and "-p" not in args:
            args = [*args[:2], "-p", self.package_name, *args[2:]]
        args = [*args, extra_type, extra_name, action.text or ""]
        self.reporter.event("semantic_slot_selected", agent=self.name, slot=action.slot, text=action.text, reason=action.reason)
        self.adb.launch_shell(args)
        self.adb.settle(float(slot_config.get("settle_seconds", 3.0)))
        self.assigned_slots[action.slot or ""] = action.text or ""
        self.session_memory.append(f"Assigned {action.slot}: {action.text}")
        return f"set_slot:{action.slot}"

    def _set_slot_with_ui_search_suggestion(self, action: AgentAction, nodes: list[Any], slot_config: dict[str, Any]) -> str:
        entry_text = str(slot_config.get("entry_text", ""))
        if not entry_text:
            raise ActionError(f"ui_search_suggestion slot requires entry_text: {action.slot}")
        entry = self._find_row_action(nodes, entry_text)
        if not entry:
            raise AdbError(f"semantic slot entry is not visible: {entry_text}")
        x, y = entry.bounds.center
        self.reporter.event("semantic_slot_selected", agent=self.name, slot=action.slot, text=action.text, reason=action.reason)
        self.adb.tap(x, y)
        self.adb.settle(float(slot_config.get("open_settle_seconds", 1.0)))

        slot_dir = self.reporter.run_dir / self.name / "semantic_slots" / (action.slot or "slot").replace(".", "_")
        search_nodes = parse_ui_xml(self.adb.dump_ui(slot_dir / "search.xml"))
        input_target = find_node(search_nodes, editable_only=True) or find_node(search_nodes, target_text=str(slot_config.get("input_text", entry_text)))
        if not input_target:
            raise AdbError(f"search field is not visible for semantic slot: {action.slot}")
        x, y = input_target.bounds.center
        self.adb.tap(x, y)
        self.adb.settle(0.3)
        self.adb.input_text(action.text or "")
        self.adb.settle(float(slot_config.get("suggestion_settle_seconds", 2.0)))

        suggestion_nodes = parse_ui_xml(self.adb.dump_ui(slot_dir / "suggestions.xml"))
        suggestion = self._find_suggestion_action(suggestion_nodes, action.text or "", slot_config)
        if not suggestion:
            raise AdbError(f"no semantic slot suggestion matched: {action.text}")
        x, y = suggestion.bounds.center
        self.adb.tap(x, y)
        self.adb.settle(float(slot_config.get("select_settle_seconds", 2.0)))
        self.assigned_slots[action.slot or ""] = action.text or ""
        self.session_memory.append(f"Assigned {action.slot}: {action.text}")
        return f"set_slot:{action.slot}"

    def _find_row_action(self, nodes: list[Any], text: str) -> Any | None:
        label = find_node(nodes, target_text=text)
        if not label:
            return None
        if label.clickable:
            return label
        _, label_y = label.bounds.center
        candidates = [
            node
            for node in nodes
            if node.clickable and node.enabled and abs(node.bounds.center[1] - label_y) <= 90
        ]
        if not candidates:
            return label
        candidates.sort(key=lambda node: abs(node.bounds.center[0] - label.bounds.center[0]))
        return candidates[0]

    def _find_suggestion_action(self, nodes: list[Any], text: str, slot_config: dict[str, Any]) -> Any | None:
        needle = text.lower()
        min_y = int(slot_config.get("suggestion_min_y", 0))
        excluded_resource_substrings = tuple(slot_config.get("exclude_resource_substrings", []))
        matching_labels = [
            node
            for node in nodes
            if needle in node.label.lower()
            and not node.editable
            and node.bounds.center[1] > min_y
            and not any(excluded in node.resource_id for excluded in excluded_resource_substrings)
        ]
        for label in matching_labels:
            _, label_y = label.bounds.center
            candidates = [
                node
                for node in nodes
                if node.clickable and node.enabled and abs(node.bounds.center[1] - label_y) <= 90
            ]
            if candidates:
                candidates.sort(key=lambda node: abs(node.bounds.center[0] - label.bounds.center[0]))
                return candidates[0]
        return None

    def run(self, subtask: SubTask, out_dir: Path) -> AgentRunResult:
        self.reporter.event("agent_start", agent=self.name, message=subtask.instruction)
        self.launch(subtask)
        for step in range(1, subtask.max_steps + 1):
            step_dir = out_dir / self.name / f"step_{step:02d}"
            xml_path = self.adb.dump_ui(step_dir / "window_dump.xml")
            screenshot_path = self.adb.screenshot(step_dir / "screenshot.png")
            nodes = parse_ui_xml(xml_path)
            ui_text = prompt_snapshot(nodes)
            (step_dir / "visible_texts.json").write_text(
                json.dumps(visible_texts(nodes), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            try:
                action = self.decide_action(subtask=subtask, step=step, ui_text=ui_text, step_dir=step_dir)
                status = self.execute_action(action, nodes)
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=action.to_json(),
                    status=status,
                    reason=action.reason,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                    visible_texts=visible_texts(nodes, limit=20),
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
                    self.session_memory.append(f"Paused for runtime request {request.request_id}: {request.need}")
                    return AgentRunResult(status="waiting", request=request, message=action.reason)
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
                        self.reporter.event("agent_finish", agent=self.name, message=f"failed verification: {verify_message}")
                        return AgentRunResult(status="failed", message=verify_message)
                    self.reporter.event("agent_finish", agent=self.name, message="finished")
                    return AgentRunResult(status="finished")
                self.adb.settle(1.2)
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
                    visible_texts=visible_texts(nodes, limit=30),
                )
                self.reporter.event("agent_finish", agent=self.name, message=f"failed: {exc}")
                return AgentRunResult(status="failed", message=str(exc))
        self.reporter.event("agent_finish", agent=self.name, message="max steps reached")
        return AgentRunResult(status="failed", message="max steps reached")

    def receive_information(self, response: RuntimeInformationResponse) -> None:
        if any(existing.request_id == response.request_id for existing in self.received_information):
            return
        self.received_information.append(response)
        self.session_memory.append(f"Received {response.request_id} from {response.from_agent}: {response.information}")
        self.reporter.event("agent_resumed", agent=self.name, response=response)

    def handle_information_request(self, request: RuntimeInformationRequest, out_dir: Path) -> RuntimeInformationResponse:
        self.reporter.event("runtime_request_received", agent=self.name, request=request)
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
            xml_path = self.adb.dump_ui(step_dir / "window_dump.xml")
            screenshot_path = self.adb.screenshot(step_dir / "screenshot.png")
            nodes = parse_ui_xml(xml_path)
            ui_text = prompt_snapshot(nodes)
            try:
                action = self.decide_action(
                    subtask=subtask,
                    step=step,
                    ui_text=ui_text,
                    step_dir=step_dir,
                    incoming_request=request,
                )
                status = self.execute_action(action, nodes)
                self.reporter.event(
                    "agent_step",
                    agent=self.name,
                    step=step,
                    action=action.to_json(),
                    status=status,
                    reason=action.reason,
                    xml=str(xml_path),
                    screenshot=str(screenshot_path),
                    visible_texts=visible_texts(nodes, limit=20),
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
                    self.reporter.event("runtime_response_created", response=response, visible_texts=visible_texts(nodes, limit=30))
                    return response
                self.adb.settle(1.2)
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
                    visible_texts=visible_texts(nodes, limit=30),
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
        return response


StaffAgent = AppStaffAgent


class StewardAgent:
    def __init__(
        self,
        agents: dict[str, AppStaffAgent],
        reporter: RunReporter,
        task_plans: dict[str, TaskPlan],
        mode: str = "steward",
    ) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.mode = mode
        configs = [agent.config for agent in agents.values()]
        for agent in agents.values():
            agent.available_peers = [config for config in configs if config.name != agent.config.name]

    def plan(self, task: str) -> TaskPlan:
        if task not in self.task_plans:
            raise ValueError(f"unsupported task: {task}")
        configured = self.task_plans[task]
        plan = TaskPlan(
            task_id=configured.task_id,
            goal=configured.goal,
            subtasks=configured.subtasks,
            edges=configured.edges,
            mode=self.mode,
        )
        self.reporter.event(
            "steward_plan",
            message=" -> ".join(subtask.agent_name for subtask in plan.subtasks),
            task_plan=plan,
        )
        self.reporter.event("task_plan_created", task_plan=plan)
        return plan

    def resolve_information_request(self, request: RuntimeInformationRequest, run_dir: Path) -> RuntimeInformationResponse:
        if self.mode == "steward":
            self.reporter.event("runtime_request_routed", mode=self.mode, via="steward", request=request)
        else:
            self.reporter.event("runtime_request_routed", mode=self.mode, via="peer", request=request)
        target_agent = self.agents[request.to_agent.removesuffix("_agent")]
        response = target_agent.handle_information_request(request, run_dir)
        if self.mode == "steward":
            self.reporter.event("runtime_response_delivered", mode=self.mode, via="steward", response=response)
        else:
            self.reporter.event("runtime_response_delivered", mode=self.mode, via="peer", response=response)
        return response

    def run(self, task: str, run_dir: Path) -> bool:
        plan = self.plan(task)
        for subtask in plan.subtasks:
            agent = self.agents[subtask.agent_name]
            result = agent.run(subtask, run_dir)
            while result.status == "waiting" and result.request:
                response = self.resolve_information_request(result.request, run_dir)
                if response.status != "success":
                    self.reporter.event("error", message=f"runtime information request failed: {response}")
                    return False
                agent.receive_information(response)
                result = agent.run(subtask, run_dir)
            if result.status != "finished":
                self.reporter.event("error", message=f"{subtask.agent_name} failed")
                return False
        return True
