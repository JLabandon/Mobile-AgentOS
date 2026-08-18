from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from mobile_agent_os.kernel.scheduler import AgentRunSpec, FifoJobScheduler, IPCSpec
from mobile_agent_os.kernel.jobs import JobResult, JobType


@dataclass(frozen=True)
class FakeAgent:
    name: str
    display_id: int


class FakeReporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def event(self, kind: str, **payload: object) -> None:
        self.events.append((kind, payload))

    def state_event(self, agent: str, state: str, **payload: object) -> None:
        self.events.append(("state", {"agent": agent, "state": state, **payload}))


class FakeExecutor:
    def __init__(self, root: Path) -> None:
        self.root = root

    def launch_agent(self, agent: FakeAgent) -> None:
        return None

    def observation_job(self, *, agent: FakeAgent, phase: str, step: int) -> JobResult:
        path = self.root / f"{agent.name}_{phase}_{step}.png"
        path.write_bytes(b"fake")
        return JobResult(f"obs-{agent.name}-{step}", JobType.OBSERVATION, agent.name, True, {"screenshot": str(path)})

    def thinking_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, instruction: str, memory: str) -> JobResult:
        return JobResult(f"think-{agent.name}-{step}", JobType.THINKING, agent.name, True, {"action": {"action": "complete", "message": f"{agent.name} done"}})

    def action_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, action: dict[str, object]) -> JobResult:
        raise AssertionError("complete action should not schedule ActionJob")

    def settle_job(self, *, agent: FakeAgent, phase: str, step: int) -> JobResult:
        raise AssertionError("complete action should not schedule SettleWaitJob")

    def ipc_delivery_job(self, *, mode: str, request_id: str, source: FakeAgent, target: FakeAgent, message: str, payload: dict[str, object], request_summary: str = "") -> JobResult:
        return JobResult(f"ipc-{request_id}", JobType.IPC_DELIVERY, "runtime", True, {"message": message, "payload": payload})


def test_fifo_scheduler_unblocks_dependent_run_after_ipc() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        source = FakeAgent("source_agent", 1)
        target = FakeAgent("target_agent", 2)
        scheduler = FifoJobScheduler(
            executor=FakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="test_agentos",
            max_workers=2,
            resource_capacity={"llm_worker:pool": 2},
        )
        result = scheduler.run(
            specs=[
                AgentRunSpec("source_run", source, "provide info", "source"),
                AgentRunSpec("target_run", target, "consume info", "target", depends_on=("source_run",)),
            ],
            ipc_specs=[
                IPCSpec("info_request", "source_run", "target_run", source, target, payload_on_success={"answer": "ready"}),
            ],
        )
    assert result.success
    assert result.outcomes["source_run"].ok
    assert result.outcomes["target_run"].ok
    ready_events = [payload for kind, payload in reporter.events if kind == "job_ready"]
    assert any(event.get("request_id") == "info_request" for event in ready_events)
    assert any(event.get("run_id") == "target_run" for event in ready_events)

class Stage6FakeExecutor(FakeExecutor):
    def thinking_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, instruction: str, memory: str) -> JobResult:
        if "IPC context" in memory:
            return JobResult(
                f"think-{agent.name}-{phase}-{step}",
                JobType.THINKING,
                agent.name,
                True,
                {"action": {"action": "complete", "message": f"{agent.name} completed with peer response"}},
            )
        if "needs late peer" in instruction:
            return JobResult(
                f"think-{agent.name}-{phase}-{step}",
                JobType.THINKING,
                agent.name,
                True,
                {
                    "action": {
                        "action": "request_information",
                        "target_agent": "shared_agent",
                        "need": f"peer information for {phase}",
                        "resume_instruction": "resume after peer response",
                    }
                },
            )
        if phase == "late_bound_response":
            return JobResult(
                f"think-{agent.name}-{phase}-{step}",
                JobType.THINKING,
                agent.name,
                True,
                {"action": {"action": "complete", "message": f"{agent.name} answered {instruction}"}},
            )
        return super().thinking_job(agent=agent, phase=phase, step=step, screenshot=screenshot, instruction=instruction, memory=memory)


def test_stage6_late_bound_information_request_resumes_requester() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        requester = FakeAgent("requester_agent", 1)
        shared = FakeAgent("shared_agent", 2)
        scheduler = FifoJobScheduler(
            executor=Stage6FakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="stage6_agentos",
            max_workers=3,
            resource_capacity={"llm_worker:pool": 3},
            service_agents={"shared_agent": shared},
        )
        result = scheduler.run(
            specs=[AgentRunSpec("requester_run", requester, "needs late peer information", "requester")],
            ipc_specs=[],
        )

    assert result.success
    assert result.outcomes["requester_run"].ok
    assert any(kind == "late_bound_request_created" for kind, _ in reporter.events)
    assert any(kind == "late_bound_response_delivered" for kind, _ in reporter.events)
    assert any(kind == "state" and payload.get("agent") == "requester_agent" and payload.get("state") == "WAIT_PEER" for kind, payload in reporter.events)
    assert any(run_id.startswith("shared_agent_late_bound_") for run_id in result.outcomes)


def test_stage6_two_cross_app_tasks_queue_on_shared_app_agent() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        task1 = FakeAgent("task1_agent", 1)
        task2 = FakeAgent("task2_agent", 2)
        shared = FakeAgent("shared_agent", 3)
        scheduler = FifoJobScheduler(
            executor=Stage6FakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="stage6_agentos",
            max_workers=4,
            resource_capacity={"llm_worker:pool": 4},
            service_agents={"shared_agent": shared},
        )
        result = scheduler.run(
            specs=[
                AgentRunSpec("task1_requester", task1, "needs late peer information", "task1"),
                AgentRunSpec("task2_requester", task2, "needs late peer information", "task2"),
            ],
            ipc_specs=[],
        )

    assert result.success
    late_requests = [payload for kind, payload in reporter.events if kind == "late_bound_request_created"]
    assert len(late_requests) == 2
    shared_starts = [idx for idx, (kind, payload) in enumerate(reporter.events) if kind == "service_context_started" and payload.get("agent") == "shared_agent"]
    shared_finishes = [idx for idx, (kind, payload) in enumerate(reporter.events) if kind == "service_context_finished" and payload.get("agent") == "shared_agent"]
    assert len(shared_starts) == 2
    assert len(shared_finishes) == 2
    assert shared_starts[0] < shared_finishes[0] < shared_starts[1] < shared_finishes[1]
    assert any(kind == "scheduler_blocked_service_busy" and payload.get("agent") == "shared_agent" for kind, payload in reporter.events)
