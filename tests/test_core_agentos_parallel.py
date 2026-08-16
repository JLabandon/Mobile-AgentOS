from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from mobile_agent_os.actions import AgentAction
from mobile_agent_os.agents import SubTask
from mobile_agent_os.display import ActionResult, DisplayManager, DisplaySlot
from mobile_agent_os.report import RunReporter
from mobile_agent_os.runtime_requests import RuntimeInformationResponse, RuntimeOperationRequest, RuntimeOperationResponse
from mobile_agent_os.runtime_requests import RuntimeInformationRequest
from mobile_agent_os.runtimes.agentos_parallel import AgentOSParallelRuntime
from mobile_agent_os.snapshots import ObservationSnapshot
from mobile_agent_os.task_plan import InformationFlow, TaskPlan


@dataclass
class FakeConfig:
    name: str
    label: str
    capabilities: tuple[str, ...] = ()


class SplitFakeAgent:
    def __init__(self, name: str, *, think_delay: float = 0.0, reporter: RunReporter | None = None) -> None:
        self.config = FakeConfig(name=name, label=name.title())
        self.name = f"{name}_agent"
        self.think_delay = think_delay
        self.reporter = reporter
        self.began = False
        self.actions: list[str] = []
        self.received_information: list[RuntimeInformationResponse] = []
        self.answered_information_requests: list[RuntimeInformationRequest] = []

    def display_package(self) -> str:
        return f"com.example.{self.config.name}"

    def activate_display_session(self, display_id: int) -> bool:
        self.actions.append(f"{display_id}:ACTIVATE")
        return True

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
        if self.reporter:
            self.reporter.event(
                "post_action_completion_check",
                agent=self.name,
                visible_texts=[
                    f"{self.config.name} done",
                    "Investor Check-in Location: Googleplex Agenda: roadmap review",
                ],
            )
        return ActionResult(status="finished")

    def receive_information(self, response: object) -> None:
        assert isinstance(response, RuntimeInformationResponse)
        self.received_information.append(response)

    def answer_information_from_snapshot(
        self,
        request: RuntimeInformationRequest,
        snapshot: ObservationSnapshot,
        out_dir: Path,
    ) -> RuntimeInformationResponse:
        self.answered_information_requests.append(request)
        return RuntimeInformationResponse(
            request_id=request.request_id,
            from_agent=self.name,
            to_agent=request.from_agent,
            status="success",
            information="Investor Check-in Location: Googleplex Agenda: roadmap review",
            source_app=self.config.label,
            confidence="medium",
            evidence=snapshot.visible_text,
        )


class NonFinishingSplitFakeAgent(SplitFakeAgent):
    def decide_from_snapshot(self, snapshot: ObservationSnapshot, subtask: SubTask, out_dir: Path) -> AgentAction:
        return AgentAction(action="click", target_text="Continue", reason="keep trying")

    def apply_display_action(self, display_id: int, action: AgentAction) -> ActionResult:
        self.actions.append(f"{display_id}:{action.action}")
        return ActionResult(status="ready")


class OperationRequesterFakeAgent(SplitFakeAgent):
    def __init__(self, name: str, *, reporter: RunReporter | None = None) -> None:
        super().__init__(name, reporter=reporter)
        self.received_operations: list[RuntimeOperationResponse] = []

    def decide_from_snapshot(self, snapshot: ObservationSnapshot, subtask: SubTask, out_dir: Path) -> AgentAction:
        if not self.received_operations:
            return AgentAction(
                action="REQUEST_OPERATION",
                to_agent="payment_agent",
                operation="authorize order PX-1042",
                context="order PX-1042 requires payment",
                purpose="complete the order",
                expected_result="payment approved",
                resume_instruction="finish after payment approval",
                reason="payment required",
            )
        return AgentAction(action="FINISH", reason="operation response received")

    def apply_display_action(self, display_id: int, action: AgentAction) -> ActionResult:
        self.actions.append(f"{display_id}:{action.action}")
        if action.action == "REQUEST_OPERATION":
            return ActionResult(status="waiting_operation")
        return ActionResult(status="finished")

    def receive_operation(self, response: object) -> None:
        assert isinstance(response, RuntimeOperationResponse)
        self.received_operations.append(response)


class OperationProviderFakeAgent(SplitFakeAgent):
    def handle_operation_request(self, request: RuntimeOperationRequest, out_dir: Path, *, record_ipc: bool = True) -> RuntimeOperationResponse:
        self.actions.append(f"handle:{request.operation}")
        return RuntimeOperationResponse(
            request_id=request.request_id,
            from_agent=self.name,
            to_agent=request.from_agent,
            status="success",
            result="payment approved",
            source_app=self.config.label,
            evidence="approved",
        )


def test_agentos_parallel_overlaps_thinking_and_other_agent_progress(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    calendar = SplitFakeAgent("calendar", think_delay=0.05, reporter=reporter)
    gmail = SplitFakeAgent("gmail", think_delay=0.0, reporter=reporter)
    task = TaskPlan(
        task_id="test_overlap",
        goal="prove agentos split-phase overlap",
        subtasks=(
            SubTask(agent_name="calendar", instruction="calendar work", max_steps=2),
            SubTask(agent_name="gmail", instruction="gmail work", max_steps=2),
        ),
        edges=(),
        mode="agentos_parallel",
    )
    runtime = AgentOSParallelRuntime(
        agents={"calendar": calendar, "gmail": gmail},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3), DisplaySlot(display_id=4)]),
    )

    assert runtime.run(task.task_id, tmp_path)
    assert calendar.actions == ["3:ACTIVATE", "3:ACTIVATE", "3:FINISH"]
    assert gmail.actions == ["4:ACTIVATE", "4:ACTIVATE", "4:FINISH"]

    thinking_calendar = next(event for event in reporter.state_events if event["agent"] == "calendar_agent" and event["state"] == "THINKING")
    gmail_acting = next(event for event in reporter.state_events if event["agent"] == "gmail_agent" and event["state"] == "ACTING")
    calendar_done = next(event for event in reporter.state_events if event["agent"] == "calendar_agent" and event["state"] == "DONE")
    calendar_states = [event["state"] for event in reporter.state_events if event["agent"] == "calendar_agent"]
    assert "SWITCH" in calendar_states
    assert calendar_states.index("SWITCH") < calendar_states.index("OBSERVING")
    assert thinking_calendar["t"] <= gmail_acting["t"] <= calendar_done["t"]

    assert any(event["kind"] == "display_slot_allocated" and event["display_id"] == 3 for event in reporter.events)
    assert any(event["kind"] == "display_switch" and event["agent"] == "calendar_agent" for event in reporter.events)
    assert any(event["kind"] == "llm_submitted" for event in reporter.events)
    assert any(event["kind"] == "ready_to_act" for event in reporter.events)
    assert any(event["kind"] == "action_guard" and event["result"] == "pass" for event in reporter.events)

    metrics = reporter.metrics(task=task.task_id, runtime="agentos_parallel", success=True)
    assert metrics["llm_overlap_time"] > 0
    assert "switch_time" in metrics
    assert metrics["fast_guard_pass_count"] == 2
    assert metrics["display_targeted_action_success_rate"] == 1.0


def test_agentos_parallel_runtime_delivers_finished_peer_result_by_plan_edge(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    calendar = SplitFakeAgent("calendar", think_delay=0.05, reporter=reporter)
    gmail = SplitFakeAgent("gmail", think_delay=0.0, reporter=reporter)
    task = TaskPlan(
        task_id="test_information_flow",
        goal="calendar uses information found by gmail",
        subtasks=(
            SubTask(agent_name="calendar", instruction="calendar work", max_steps=2),
            SubTask(agent_name="gmail", instruction="gmail provider work", max_steps=2),
        ),
        edges=(("gmail", "calendar"),),
        information_flows=(InformationFlow("gmail", "calendar", name="meeting_details", fields=("location", "agenda")),),
        mode="agentos_parallel",
    )
    runtime = AgentOSParallelRuntime(
        agents={"calendar": calendar, "gmail": gmail},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3), DisplaySlot(display_id=4)]),
    )

    assert runtime.run(task.task_id, tmp_path)
    assert calendar.received_information
    assert calendar.received_information[0].from_agent == "gmail_agent"
    assert calendar.received_information[0].to_agent == "calendar_agent"
    assert "Googleplex" in calendar.received_information[0].information
    assert "meeting_details" in calendar.received_information[0].request_id
    assert any(event["kind"] == "peer_result_delivered" and event["via"] == "peer" for event in reporter.events)
    assert any(event["request_summary"] == "meeting_details: location, agenda" for event in reporter.ipc_events)


def test_shared_foreground_runtime_runs_edge_source_before_target(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    calendar = SplitFakeAgent("calendar", think_delay=0.0, reporter=reporter)
    gmail = SplitFakeAgent("gmail", think_delay=0.0, reporter=reporter)
    task = TaskPlan(
        task_id="test_foreground_ordering",
        goal="calendar depends on gmail in a single foreground display",
        subtasks=(
            SubTask(agent_name="calendar", instruction="calendar work", max_steps=2),
            SubTask(agent_name="gmail", instruction="gmail provider work", max_steps=2),
        ),
        edges=(("gmail", "calendar"),),
        mode="agentos_parallel",
    )
    display_manager = DisplayManager(
        [
            DisplaySlot(display_id=0, observation_channel="foreground_uiautomator"),
            DisplaySlot(display_id=0, observation_channel="foreground_uiautomator"),
        ]
    )
    runtime = AgentOSParallelRuntime(
        agents={"calendar": calendar, "gmail": gmail},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=display_manager,
    )

    assert runtime.run(task.task_id, tmp_path)
    gmail_observed = next(event for event in reporter.events if event["kind"] == "snapshot_created" and event["agent"] == "gmail_agent")
    calendar_observed = next(event for event in reporter.events if event["kind"] == "snapshot_created" and event["agent"] == "calendar_agent")
    assert gmail_observed["time"] <= calendar_observed["time"]


def test_agentos_parallel_runtime_enforces_subtask_max_steps(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    clock = NonFinishingSplitFakeAgent("clock", reporter=reporter)
    task = TaskPlan(
        task_id="test_max_steps",
        goal="stop repeated non-terminal actions",
        subtasks=(SubTask(agent_name="clock", instruction="clock work", max_steps=2),),
        mode="agentos_parallel",
    )
    runtime = AgentOSParallelRuntime(
        agents={"clock": clock},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3)]),
    )

    assert not runtime.run(task.task_id, tmp_path)
    assert clock.actions == ["3:ACTIVATE", "3:ACTIVATE", "3:click", "3:ACTIVATE", "3:ACTIVATE", "3:click"]
    assert any(event["kind"] == "agent_finish" and event["agent"] == "clock_agent" and event["max_steps"] == 2 for event in reporter.events)
    assert any(event["agent"] == "clock_agent" and event["state"] == "FAILED" and "max steps reached" in event["message"] for event in reporter.state_events)


def test_agentos_parallel_runtime_routes_operation_request_to_peer(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    shop = OperationRequesterFakeAgent("shop", reporter=reporter)
    payment = OperationProviderFakeAgent("payment", reporter=reporter)
    task = TaskPlan(
        task_id="test_operation_request",
        goal="complete order after payment",
        subtasks=(SubTask(agent_name="shop", instruction="shop work", max_steps=3),),
        mode="agentos_parallel",
    )
    runtime = AgentOSParallelRuntime(
        agents={"shop": shop, "payment": payment},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3)]),
    )

    assert runtime.run(task.task_id, tmp_path)
    assert shop.received_operations[0].result == "payment approved"
    assert payment.actions == ["handle:authorize order PX-1042"]
    assert any(event["kind"] == "runtime_operation_request_routed" for event in reporter.events)
    assert any(event["message_kind"] == "RuntimeOperationResponse" and event["status"] == "delivered" for event in reporter.ipc_events)


def test_planner_declared_flow_uses_provider_agent_response_synthesis(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    keep = SplitFakeAgent("keep", reporter=reporter)
    calendar = SplitFakeAgent("calendar", reporter=reporter)
    task = TaskPlan(
        task_id="test_provider_synthesis",
        goal="calendar uses note information",
        subtasks=(
            SubTask(agent_name="keep", instruction="find the note", max_steps=2),
            SubTask(agent_name="calendar", instruction="use the note", max_steps=2),
        ),
        edges=(("keep", "calendar"),),
        information_flows=(InformationFlow("keep", "calendar", name="note_details", fields=("title", "location")),),
        mode="agentos_parallel",
    )
    runtime = AgentOSParallelRuntime(
        agents={"keep": keep, "calendar": calendar},
        reporter=reporter,
        task_plans={task.task_id: task},
        display_manager=DisplayManager([DisplaySlot(display_id=3), DisplaySlot(display_id=4)]),
    )

    assert runtime.run(task.task_id, tmp_path)
    assert keep.answered_information_requests
    assert keep.answered_information_requests[0].from_agent == "calendar_agent"
    assert keep.answered_information_requests[0].to_agent == "keep_agent"
    assert "note_details" in keep.answered_information_requests[0].need
    assert calendar.received_information
    assert calendar.received_information[0].from_agent == "keep_agent"
