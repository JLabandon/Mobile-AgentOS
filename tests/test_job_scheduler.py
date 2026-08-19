from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from mobile_agent_os.kernel.scheduler import AgentRunSpec, FifoJobScheduler, IPCSpec
from mobile_agent_os.kernel.jobs import JobResult, JobType
from mobile_agent_os.kernel.scheduler_policy import CriticalPathSchedulingPolicy, FifoSchedulingPolicy, JobCandidate, SchedulerSnapshot


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
        self.launched_agents: list[str] = []

    def launch_agent(self, agent: FakeAgent) -> None:
        self.launched_agents.append(agent.name)
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


def test_fifo_policy_preserves_ready_order() -> None:
    snapshot = SchedulerSnapshot(
        candidates=(
            JobCandidate(token=2, kind="agent", run_id="second"),
            JobCandidate(token=1, kind="agent", run_id="first"),
        )
    )

    ordered = FifoSchedulingPolicy().order(snapshot)

    assert [candidate.run_id for candidate in ordered] == ["first", "second"]


def test_serial_order_blocks_later_runs_until_previous_run_finishes() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        first = FakeAgent("first_agent", 1)
        second = FakeAgent("second_agent", 2)
        scheduler = FifoJobScheduler(
            executor=FakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="test_serial",
            max_workers=2,
            resource_capacity={"llm_worker:pool": 2},
            serial_order=("first_run", "second_run"),
        )
        result = scheduler.run(
            specs=[
                AgentRunSpec("first_run", first, "complete first", "first"),
                AgentRunSpec("second_run", second, "complete second", "second"),
            ],
            ipc_specs=[],
        )

    assert result.success
    done_first = next(index for index, (kind, payload) in enumerate(reporter.events) if kind == "state" and payload.get("agent") == "first_agent" and payload.get("state") == "DONE")
    ready_second = next(index for index, (kind, payload) in enumerate(reporter.events) if kind == "job_ready" and payload.get("run_id") == "second_run")
    assert done_first < ready_second


def test_critical_path_policy_prefers_runs_with_more_successors() -> None:
    snapshot = SchedulerSnapshot(
        candidates=(
            JobCandidate(token=1, kind="agent", run_id="short_leaf", estimated_duration_s=0.1),
            JobCandidate(token=2, kind="agent", run_id="critical_root", estimated_duration_s=2.0),
        )
    )

    ordered = CriticalPathSchedulingPolicy({"critical_root": 3}).order(snapshot)

    assert [candidate.run_id for candidate in ordered] == ["critical_root", "short_leaf"]


def test_scheduler_records_policy_decision() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        first = FakeAgent("first_agent", 1)
        second = FakeAgent("second_agent", 2)
        scheduler = FifoJobScheduler(
            executor=FakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="test_agentos",
            max_workers=2,
            resource_capacity={"llm_worker:pool": 2},
        )
        result = scheduler.run(
            specs=[
                AgentRunSpec("first_run", first, "complete first", "first"),
                AgentRunSpec("second_run", second, "complete second", "second"),
            ],
            ipc_specs=[],
        )

    assert result.success
    decisions = [payload for kind, payload in reporter.events if kind == "scheduler_policy_decision"]
    assert decisions
    assert decisions[0]["policy"] == "fifo"

class LateBoundFakeExecutor(FakeExecutor):
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


def test_serial_order_allows_late_bound_provider_run() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        requester = FakeAgent("requester_agent", 1)
        shared = FakeAgent("shared_agent", 2)
        scheduler = FifoJobScheduler(
            executor=LateBoundFakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="test_serial",
            max_workers=1,
            resource_capacity={"llm_worker:pool": 1},
            service_agents={"shared_agent": shared},
            serial_order=("requester_run",),
        )
        result = scheduler.run(
            specs=[AgentRunSpec("requester_run", requester, "needs late peer", "requester")],
            ipc_specs=[],
        )

    assert result.success
    assert result.outcomes["requester_run"].ok
    assert any(run_id.startswith("shared_agent_late_bound_") for run_id in result.outcomes)


def test_late_bound_information_request_resumes_requester() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        requester = FakeAgent("requester_agent", 1)
        shared = FakeAgent("shared_agent", 2)
        scheduler = FifoJobScheduler(
            executor=LateBoundFakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="agentos",
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


def test_two_cross_app_tasks_queue_on_shared_app_agent() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        task1 = FakeAgent("task1_agent", 1)
        task2 = FakeAgent("task2_agent", 2)
        shared = FakeAgent("shared_agent", 3)
        scheduler = FifoJobScheduler(
            executor=LateBoundFakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="agentos",
            max_workers=4,
            resource_capacity={"llm_worker:pool": 4, "display_slot:task_hosting": 2},
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


class MultiInstanceFakeExecutor(LateBoundFakeExecutor):
    def create_agent_instance(self, agent: FakeAgent, *, service_name: str, run_id: str, instance_index: int) -> FakeAgent:
        return FakeAgent(f"{service_name}#{instance_index + 1}", 30 + instance_index)


def test_parallel_provider_instances_when_registry_allows() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        task1 = FakeAgent("task1_agent", 1)
        task2 = FakeAgent("task2_agent", 2)
        shared = FakeAgent("shared_agent", 3)
        scheduler = FifoJobScheduler(
            executor=MultiInstanceFakeExecutor(Path(tmp)),
            reporter=reporter,
            mode="agentos",
            max_workers=4,
            resource_capacity={"llm_worker:pool": 4, "display_slot:task_hosting": 2},
            service_agents={"shared_agent": shared},
            service_capacity={"shared_agent": 2},
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
    created_instances = [payload for kind, payload in reporter.events if kind == "service_instance_created"]
    assert len(created_instances) == 2
    assert [item["instance_agent"] for item in created_instances] == ["shared_agent#1", "shared_agent#2"]
    assert any(payload.get("capacity") == 2 for kind, payload in reporter.events if kind == "service_context_started" and payload.get("agent") == "shared_agent")
    assert not any(kind == "scheduler_blocked_service_busy" and payload.get("agent") == "shared_agent" for kind, payload in reporter.events)


class FinalOracleExecutor(FakeExecutor):
    def thinking_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, instruction: str, memory: str) -> JobResult:
        return JobResult(
            f"think-{agent.name}-{step}",
            JobType.THINKING,
            agent.name,
            True,
            {"action": {"action": "click", "x": 1, "y": 1}},
        )

    def action_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, action: dict[str, object]) -> JobResult:
        return JobResult(f"act-{agent.name}-{step}", JobType.ACTION, agent.name, True, {"memory": "clicked"})

    def settle_job(self, *, agent: FakeAgent, phase: str, step: int) -> JobResult:
        return JobResult(f"settle-{agent.name}-{step}", JobType.SETTLE_WAIT, agent.name, True, {"seconds": 0})

    def completion_check(self, agent: FakeAgent) -> str | None:
        if agent.name == "final_agent":
            return "final app status complete"
        return None


def test_scheduler_stops_when_final_run_oracle_matches() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        final_agent = FakeAgent("final_agent", 1)
        extra_agent = FakeAgent("extra_agent", 2)
        scheduler = FifoJobScheduler(
            executor=FinalOracleExecutor(Path(tmp)),
            reporter=reporter,
            mode="test_serial",
            max_workers=1,
            resource_capacity={"llm_worker:pool": 1},
            serial_order=("final_run", "extra_run"),
            final_run_id="final_run",
        )
        result = scheduler.run(
            specs=[
                AgentRunSpec("final_run", final_agent, "finish final", "final"),
                AgentRunSpec("extra_run", extra_agent, "unneeded after final", "extra"),
            ],
            ipc_specs=[],
        )

    assert result.success
    assert result.outcomes["final_run"].ok
    assert "extra_run" not in result.outcomes
    assert any(kind == "runtime_final_completion_oracle_matched" for kind, _ in reporter.events)


class TwoStepExecutor(FakeExecutor):
    def thinking_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, instruction: str, memory: str) -> JobResult:
        if step == 1:
            return JobResult(
                f"think-{agent.name}-{step}",
                JobType.THINKING,
                agent.name,
                True,
                {"action": {"action": "click", "x": 1, "y": 1}},
            )
        return JobResult(
            f"think-{agent.name}-{step}",
            JobType.THINKING,
            agent.name,
            True,
            {"action": {"action": "complete", "message": f"{agent.name} done"}},
        )

    def action_job(self, *, agent: FakeAgent, phase: str, step: int, screenshot: Path, action: dict[str, object]) -> JobResult:
        return JobResult(f"act-{agent.name}-{step}", JobType.ACTION, agent.name, True, {"memory": "clicked"})

    def settle_job(self, *, agent: FakeAgent, phase: str, step: int) -> JobResult:
        return JobResult(f"settle-{agent.name}-{step}", JobType.SETTLE_WAIT, agent.name, True, {"seconds": 0})


def test_primary_display_agent_is_resumed_before_each_observation() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        executor = TwoStepExecutor(Path(tmp))
        agent = FakeAgent("primary_agent", 0)
        scheduler = FifoJobScheduler(
            executor=executor,
            reporter=reporter,
            mode="agentos",
            max_workers=1,
            resource_capacity={"llm_worker:pool": 1},
        )
        result = scheduler.run(specs=[AgentRunSpec("primary_run", agent, "two step task", "primary")], ipc_specs=[])

    assert result.success
    assert executor.launched_agents == ["primary_agent", "primary_agent"]


def test_task_hosting_display_agent_is_not_relaunched_between_steps() -> None:
    with TemporaryDirectory() as tmp:
        reporter = FakeReporter()
        executor = TwoStepExecutor(Path(tmp))
        agent = FakeAgent("display_agent", 49)
        scheduler = FifoJobScheduler(
            executor=executor,
            reporter=reporter,
            mode="agentos",
            max_workers=1,
            resource_capacity={"llm_worker:pool": 1},
        )
        result = scheduler.run(specs=[AgentRunSpec("display_run", agent, "two step task", "display")], ipc_specs=[])

    assert result.success
    assert executor.launched_agents == ["display_agent"]
