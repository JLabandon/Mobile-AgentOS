from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .actions import ActionError, AgentAction
from .adb import AdbClient, AdbError
from .llm import DeepSeekClient, LlmError
from .report import RunReporter
from .ui_tree import find_node, parse_ui_xml, prompt_snapshot, visible_texts


@dataclass(frozen=True)
class AppConfig:
    name: str
    label: str
    package_candidates: list[str]
    launch: dict[str, Any]


@dataclass(frozen=True)
class SubTask:
    agent_name: str
    instruction: str
    max_steps: int = 6


class StaffAgent:
    def __init__(self, *, config: AppConfig, adb: AdbClient, llm: DeepSeekClient, reporter: RunReporter) -> None:
        self.config = config
        self.adb = adb
        self.llm = llm
        self.reporter = reporter
        self.package_name: str | None = None

    @property
    def name(self) -> str:
        return f"{self.config.name}_agent"

    def launch(self) -> None:
        self.package_name = self.adb.pick_package(self.config.package_candidates)
        launch = self.config.launch
        if launch.get("mode") == "intent":
            self.adb.launch_shell([str(arg) for arg in launch.get("args", [])])
        else:
            self.adb.launch_package(self.package_name)
        self.adb.settle(3.0)

    def system_prompt(self) -> str:
        return (
            "You are a mobile app StaffAgent. Return JSON only. "
            "You may choose exactly one action from click, input, swipe, back, FINISH. "
            "Use only the UI nodes given by the user. Do not save or create final records. "
            "Schema: {\"action\":\"click|input|swipe|back|FINISH\","
            "\"target_id\":0,\"target_text\":\"optional\",\"text\":\"optional\","
            "\"direction\":\"up|down|left|right\",\"reason\":\"short\"}."
        )

    def user_prompt(self, *, instruction: str, step: int, ui_text: str) -> str:
        return (
            f"Agent: {self.name}\n"
            f"App: {self.config.label}\n"
            f"Instruction: {instruction}\n"
            f"Step: {step}\n"
            "Important: default demo mode is draft-only. Do not tap Save, Done, Create, or final confirmation.\n"
            "If the visible draft fields look sufficiently filled or no safe non-saving action remains, return FINISH.\n"
            "Visible UI nodes:\n"
            f"{ui_text}\n"
            "Return one JSON action."
        )

    def decide_action(self, *, instruction: str, step: int, ui_text: str) -> AgentAction:
        last_error: str | None = None
        for attempt in range(2):
            try:
                result = self.llm.json_chat(
                    system=self.system_prompt(),
                    user=self.user_prompt(instruction=instruction, step=step, ui_text=ui_text)
                    + (f"\nPrevious JSON error: {last_error}" if last_error else ""),
                )
                return AgentAction.from_json(result)
            except (LlmError, ActionError) as exc:
                last_error = str(exc)
                self.reporter.event(
                    "model_retry",
                    agent=self.name,
                    step=step,
                    attempt=attempt + 1,
                    message=last_error,
                )
        raise ActionError(f"failed to obtain valid action after retry: {last_error}")

    def execute_action(self, action: AgentAction, nodes: list[Any]) -> str:
        if action.action == "FINISH":
            return "finished"
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

    def run(self, subtask: SubTask, out_dir: Path) -> bool:
        self.reporter.event("agent_start", agent=self.name, message=subtask.instruction)
        self.launch()
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
                action = self.decide_action(instruction=subtask.instruction, step=step, ui_text=ui_text)
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
                if action.action == "FINISH":
                    self.reporter.event("agent_finish", agent=self.name, message="finished")
                    return True
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
                return False
        self.reporter.event("agent_finish", agent=self.name, message="max steps reached")
        return False


class CalendarAgent(StaffAgent):
    pass


class ClockAgent(StaffAgent):
    pass


class StewardAgent:
    def __init__(self, agents: dict[str, StaffAgent], reporter: RunReporter) -> None:
        self.agents = agents
        self.reporter = reporter

    def plan(self, task: str) -> list[SubTask]:
        if task != "calendar_clock_draft":
            raise ValueError(f"unsupported task: {task}")
        subtasks = [
            SubTask(
                agent_name="calendar",
                instruction=(
                    "Open a draft calendar event for 'Agent IPC MVP Meeting'. "
                    "Fill obvious title/time fields if they are visible. Do not save the event."
                ),
            ),
            SubTask(
                agent_name="clock",
                instruction=(
                    "Open a draft alarm for 09:30 with label 'Agent IPC MVP Meeting'. "
                    "Fill obvious time/label fields if they are visible. Do not save the alarm."
                ),
            ),
        ]
        self.reporter.event(
            "steward_plan",
            message=" -> ".join(subtask.agent_name for subtask in subtasks),
            subtasks=[subtask.__dict__ for subtask in subtasks],
        )
        return subtasks

    def run(self, task: str, run_dir: Path) -> bool:
        for subtask in self.plan(task):
            agent = self.agents[subtask.agent_name]
            ok = agent.run(subtask, run_dir)
            if not ok:
                self.reporter.event("error", message=f"{subtask.agent_name} failed")
                return False
        return True
