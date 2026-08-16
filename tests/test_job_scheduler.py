from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from mobile_agent_os.job_scheduler import AgentRunSpec, FifoJobScheduler, IPCSpec
from mobile_agent_os.jobs import JobResult, JobType


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
