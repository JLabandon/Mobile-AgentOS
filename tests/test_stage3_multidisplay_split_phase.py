from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from mobile_agent_os.actions import AgentAction
from mobile_agent_os.agents import SubTask
from mobile_agent_os.display import ActionResult, DisplayManager, DisplaySlot
from mobile_agent_os.report import RunReporter
from mobile_agent_os.runtime_requests import RuntimeInformationResponse
from mobile_agent_os.runtimes.multidisplay_split_phase import MultidisplaySplitPhaseRuntime
from mobile_agent_os.snapshots import ObservationSnapshot
from mobile_agent_os.task_plan import TaskPlan


@dataclass
class FakeConfig:
    name: str
    label: str
    capabilities: tuple[str, ...] = ()


class SplitFakeAgent:
    def __init__(self, name: str, *, think_delay: float = 0.0) -> None:
        self.config = FakeConfig(name=name, label=name.title())
        self.name = f"{name}_agent"
        self.think_delay = think_delay
        self.began = False
        self.actions: list[str] = []
        self.received_information: list[RuntimeInformationResponse] = []

    def display_package(self) -> str:
        return f"com.example.{self.config.name}"

    def begin_task(self, subtask: SubTask, out_dir: Path) -> None:
        self.began = True

    def observe_display(self, display_id: int) -> ObservationSnapshot:
        return ObservationSnapshot.create(
            agent=self.name,
            display_id=display_id,
            app_package=self.display_package(),
            visible_text=f"{self.config.name} ready",
        )

    def decide_from_snapshot(self, snapshot: ObservationSnapshot, subtask: SubTask, out_dir: Path) -> AgentAction:
        time.sleep(self.think_delay)
        return AgentAction(action="FINISH", reason=f"{self.name} finished from {snapshot.snapshot_id}")

    def apply_display_action(self, display_id: int, action: AgentAction) -> ActionResult:
        time.sleep(0.02)
        self.actions.append(f"{display_id}:{action.action}")
        return ActionResult(status="finished")

    def receive_information(self, response: object) -> None:
        assert isinstance(response, RuntimeInformationResponse)
        self.received_information.append(response)


def test_multidisplay_split_phase_overlaps_thinking_and_other_agent_progress(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    calendar = SplitFakeAgent("calendar", think_delay=0.05)
    gmail = SplitFakeAgent("gmail", think_delay=0.0)
    task = TaskPlan(
        task_id="fake_stage3",
        goal="prove split phase overlap",
        subtasks=(
            SubTask(agent_name="calendar", instruction="calendar work", max_steps=2),
            SubTask(agent_name="gmail", instruction="gmail work", max_steps=2),
        ),
        edges=(),
        mode="multidisplay_split_phase",
    )
    runtime = MultidisplaySplitPhaseRuntime(
        agents={"calendar": calendar, "gmail": gmail},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3), DisplaySlot(display_id=4)]),
    )

    assert runtime.run(task.task_id, tmp_path)
    assert calendar.actions == ["3:FINISH"]
    assert gmail.actions == ["4:FINISH"]

    thinking_calendar = next(event for event in reporter.state_events if event["agent"] == "calendar_agent" and event["state"] == "THINKING")
    gmail_acting = next(event for event in reporter.state_events if event["agent"] == "gmail_agent" and event["state"] == "ACTING")
    calendar_done = next(event for event in reporter.state_events if event["agent"] == "calendar_agent" and event["state"] == "DONE")
    assert thinking_calendar["t"] <= gmail_acting["t"] <= calendar_done["t"]

    assert any(event["kind"] == "display_slot_allocated" and event["display_id"] == 3 for event in reporter.events)
    assert any(event["kind"] == "llm_submitted" for event in reporter.events)
    assert any(event["kind"] == "ready_to_act" for event in reporter.events)
    assert any(event["kind"] == "action_guard" and event["result"] == "pass" for event in reporter.events)

    metrics = reporter.metrics(task=task.task_id, runtime="multidisplay_split_phase", success=True)
    assert metrics["llm_overlap_time"] > 0
    assert metrics["fast_guard_pass_count"] == 2
    assert metrics["display_targeted_action_success_rate"] == 1.0


def test_multidisplay_runtime_delivers_finished_peer_result_by_plan_edge(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    calendar = SplitFakeAgent("calendar", think_delay=0.05)
    gmail = SplitFakeAgent("gmail", think_delay=0.0)
    task = TaskPlan(
        task_id="fake_stage3_edge",
        goal="calendar uses information found by gmail",
        subtasks=(
            SubTask(agent_name="calendar", instruction="calendar work", max_steps=2),
            SubTask(agent_name="gmail", instruction="gmail provider work", max_steps=2),
        ),
        edges=(("gmail", "calendar"),),
        mode="multidisplay_split_phase",
    )
    runtime = MultidisplaySplitPhaseRuntime(
        agents={"calendar": calendar, "gmail": gmail},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3), DisplaySlot(display_id=4)]),
    )

    assert runtime.run(task.task_id, tmp_path)
    assert calendar.received_information
    assert calendar.received_information[0].from_agent == "gmail_agent"
    assert calendar.received_information[0].to_agent == "calendar_agent"
    assert any(event["kind"] == "peer_result_delivered" and event["via"] == "peer" for event in reporter.events)
