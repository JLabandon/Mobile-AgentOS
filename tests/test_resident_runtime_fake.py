from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mobile_agent_os.agents import AgentStepResult, SubTask
from mobile_agent_os.report import RunReporter
from mobile_agent_os.runtime_requests import RuntimeInformationRequest, RuntimeInformationResponse
from mobile_agent_os.runtimes.resident_runtime import ResidentRuntime
from mobile_agent_os.task_plan import TaskPlan


@dataclass
class FakeConfig:
    name: str
    label: str
    capabilities: tuple[str, ...]
    package_candidates: tuple[str, ...] = ()


class RequestingAgent:
    name = "calendar_agent"

    def __init__(self) -> None:
        self.config = FakeConfig("calendar", "Calendar", ("create_event",))
        self.available_peers = []
        self.received: list[RuntimeInformationResponse] = []
        self.started = False
        self.requested = False

    def begin_task(self, subtask: SubTask, out_dir: Path) -> None:
        self.started = True

    def step_task(self) -> AgentStepResult:
        if not self.requested:
            self.requested = True
            request = RuntimeInformationRequest.create(
                from_agent=self.name,
                to_agent="keep_agent",
                need="meeting location",
                context="calendar event",
                purpose="finish event",
                resume_instruction="use returned information in Calendar",
            )
            return AgentStepResult(status="waiting", request=request)
        if self.received:
            return AgentStepResult(status="finished")
        return AgentStepResult(status="ready")

    def receive_information(self, response: RuntimeInformationResponse) -> None:
        self.received.append(response)


class RespondingAgent:
    name = "keep_agent"

    def __init__(self) -> None:
        self.config = FakeConfig("keep", "Keep", ("retrieve_information", "retrieve_notes"))
        self.available_peers = []
        self.handled: list[str] = []

    def handle_information_request(
        self,
        request: RuntimeInformationRequest,
        out_dir: Path,
        *,
        record_ipc: bool = True,
    ) -> RuntimeInformationResponse:
        self.handled.append(request.request_id)
        return RuntimeInformationResponse(
            request_id=request.request_id,
            from_agent=self.name,
            to_agent=request.from_agent,
            status="success",
            information="Googleplex",
            source_app="Keep",
            confidence="high",
            evidence="Location: Googleplex",
        )


def test_resident_runtime_records_state_mailbox_and_foreground(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    requester = RequestingAgent()
    provider = RespondingAgent()
    task = TaskPlan(
        task_id="fake_calendar_keep",
        goal="calendar asks keep for runtime information",
        subtasks=(SubTask(agent_name="calendar", instruction="finish event"),),
        edges=(("calendar", "keep"),),
        mode="resident_runtime",
    )
    runtime = ResidentRuntime(
        agents={"calendar": requester, "keep": provider},  # type: ignore[arg-type]
        reporter=reporter,
        task_plans={task.task_id: task},
    )

    assert runtime.run(task.task_id, tmp_path)
    assert requester.received[0].information == "Googleplex"
    assert provider.handled == [requester.received[0].request_id]

    statuses = [event["status"] for event in reporter.query_ipc_ledger(request_id=requester.received[0].request_id)]
    assert statuses == ["created", "queued", "accepted", "success", "delivered"]
    assert any(event["kind"] == "resident_registry" for event in reporter.events)
    assert any(event["kind"] == "resident_state_snapshot" for event in reporter.events)
    assert any(event["kind"] == "foreground_interaction" for event in reporter.events)
    assert any(event["kind"] == "capability_route" for event in reporter.events)
