from __future__ import annotations

import json
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .jobs import JobResult, JobType


class ScheduledRunStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AgentRunSpec:
    run_id: str
    agent: Any
    instruction: str
    phase: str
    depends_on: tuple[str, ...] = ()
    max_steps: int = 6
    launch: bool = True


@dataclass(frozen=True)
class IPCSpec:
    request_id: str
    source_run_id: str
    target_run_id: str
    source_agent: Any
    target_agent: Any
    payload_on_success: dict[str, Any] = field(default_factory=dict)
    payload_on_failure: dict[str, Any] = field(default_factory=dict)
    request_summary: str = ""


@dataclass
class AgentRunState:
    spec: AgentRunSpec
    status: ScheduledRunStatus = ScheduledRunStatus.PENDING
    step: int = 1
    launched: bool = False
    memory: str = ""
    last_screenshot: Path | None = None
    pending_action: dict[str, Any] | None = None
    settle_needed: bool = False
    message: str = ""
    error: str = ""


@dataclass(frozen=True)
class ScheduledOutcome:
    run_id: str
    ok: bool
    message: str
    steps: int


@dataclass(frozen=True)
class ScheduledResult:
    success: bool
    outcomes: dict[str, ScheduledOutcome]
    error: str = ""


@dataclass(frozen=True)
class QueueItem:
    kind: str
    run_id: str | None = None
    job_type: JobType | None = None
    ipc: IPCSpec | None = None
    token: int = field(default=0)


class JobExecutor(Protocol):
    def launch_agent(self, agent: Any) -> None:
        ...

    def observation_job(self, *, agent: Any, phase: str, step: int) -> JobResult:
        ...

    def thinking_job(
        self,
        *,
        agent: Any,
        phase: str,
        step: int,
        screenshot: Path,
        instruction: str,
        memory: str,
    ) -> JobResult:
        ...

    def action_job(self, *, agent: Any, phase: str, step: int, screenshot: Path, action: dict[str, Any]) -> JobResult:
        ...

    def settle_job(self, *, agent: Any, phase: str, step: int) -> JobResult:
        ...

    def ipc_delivery_job(self, *, mode: str, request_id: str, source: Any, target: Any, message: str, payload: dict[str, Any], request_summary: str = "") -> JobResult:
        ...


class TraceSink(Protocol):
    def event(self, kind: str, **payload: object) -> None:
        ...

    def state_event(self, agent: str, state: str, **payload: object) -> None:
        ...


class FifoJobScheduler:
    def __init__(
        self,
        *,
        executor: JobExecutor,
        reporter: TraceSink,
        mode: str,
        max_workers: int,
        serial_order: tuple[str, ...] = (),
        resource_capacity: dict[str, int] | None = None,
    ) -> None:
        self.executor = executor
        self.reporter = reporter
        self.mode = mode
        self.max_workers = max_workers
        self.serial_order = serial_order
        self.resource_capacity = resource_capacity or {}

    def run(self, *, specs: list[AgentRunSpec], ipc_specs: list[IPCSpec]) -> ScheduledResult:
        self.reporter.state_event(
            "runtime",
            "SCHEDULING",
            runtime=self.mode,
            reason="scheduler_start",
        )
        states = {spec.run_id: AgentRunState(spec=spec) for spec in specs}
        outcomes: dict[str, ScheduledOutcome] = {}
        ipc_by_source: dict[str, list[IPCSpec]] = {}
        ipc_delivered: set[str] = set()
        ipc_context_by_target: dict[str, list[dict[str, Any]]] = {}
        for ipc in ipc_specs:
            ipc_by_source.setdefault(ipc.source_run_id, []).append(ipc)

        queue: deque[QueueItem] = deque()
        queued: set[tuple[str, str]] = set()
        running: dict[Future[JobResult], tuple[QueueItem, list[str]]] = {}
        leased: dict[str, list[QueueItem]] = {}
        token = 0

        def resource_key(name: str, scope: object = "global") -> str:
            return f"{name}:{scope}"

        def resources_for(item: QueueItem) -> list[str]:
            if item.kind == "ipc":
                assert item.ipc is not None
                return [resource_key("ledger"), resource_key("mailbox", item.ipc.target_run_id)]
            assert item.job_type is not None and item.run_id is not None
            state = states[item.run_id]
            display_id = getattr(state.spec.agent, "display_id", "none")
            if item.job_type == JobType.OBSERVATION:
                return [resource_key("display_observation", display_id)]
            if item.job_type == JobType.THINKING:
                return [resource_key("llm_worker", "pool")]
            if item.job_type == JobType.ACTION:
                return [resource_key("display_input", display_id)]
            if item.job_type == JobType.SETTLE_WAIT:
                return [resource_key("display_settle", display_id)]
            return []

        def serial_allows(run_id: str) -> bool:
            if not self.serial_order:
                return True
            for previous in self.serial_order:
                if previous == run_id:
                    return True
                if previous not in outcomes:
                    return False
            return run_id not in self.serial_order

        def dependencies_satisfied(state: AgentRunState) -> bool:
            for dependency in state.spec.depends_on:
                outcome = outcomes.get(dependency)
                if outcome is None or not outcome.ok:
                    return False
                for ipc in ipc_by_source.get(dependency, []):
                    if ipc.target_run_id == state.spec.run_id and ipc.request_id not in ipc_delivered:
                        return False
            return True

        def memory_from_dependencies(state: AgentRunState) -> str:
            parts = [state.memory]
            for dependency in state.spec.depends_on:
                outcome = outcomes.get(dependency)
                if outcome:
                    parts.append(f"\nDependency {dependency}: {'success' if outcome.ok else 'failed'}")
            for payload in ipc_context_by_target.get(state.spec.run_id, []):
                parts.append(f"\nIPC context: {json.dumps(payload, sort_keys=True, ensure_ascii=False)}")
            return "".join(parts)

        def next_job_type(state: AgentRunState) -> JobType | None:
            if state.step > state.spec.max_steps:
                state.status = ScheduledRunStatus.FAILED
                state.error = "max steps reached"
                outcomes[state.spec.run_id] = ScheduledOutcome(state.spec.run_id, False, state.error, state.spec.max_steps)
                self.reporter.state_event(
                    getattr(state.spec.agent, "name", state.spec.run_id),
                    "FAILED",
                    runtime=self.mode,
                    phase=state.spec.phase,
                    display_id=getattr(state.spec.agent, "display_id", None),
                    reason=state.error,
                )
                return None
            if state.last_screenshot is None:
                return JobType.OBSERVATION
            if state.pending_action is None and not state.settle_needed:
                return JobType.THINKING
            if state.pending_action is not None:
                return JobType.ACTION
            if state.settle_needed:
                return JobType.SETTLE_WAIT
            return None

        def enqueue_agent_job(run_id: str) -> None:
            nonlocal token
            state = states[run_id]
            if state.status in {ScheduledRunStatus.RUNNING, ScheduledRunStatus.DONE, ScheduledRunStatus.FAILED}:
                return
            if not serial_allows(run_id) or not dependencies_satisfied(state):
                return
            job_type = next_job_type(state)
            if job_type is None:
                return
            key = (run_id, job_type.value)
            if key in queued:
                return
            state.status = ScheduledRunStatus.READY
            token += 1
            queue.append(QueueItem("agent", run_id=run_id, job_type=job_type, token=token))
            queued.add(key)
            self.reporter.event("job_ready", runtime=self.mode, run_id=run_id, job_type=job_type.value, token=token)

        def enqueue_ipc(ipc: IPCSpec) -> None:
            nonlocal token
            key = (ipc.request_id, "ipc")
            if key in queued or ipc.request_id in ipc_delivered:
                return
            token += 1
            queue.append(QueueItem("ipc", ipc=ipc, token=token))
            queued.add(key)
            self.reporter.event("job_ready", runtime=self.mode, request_id=ipc.request_id, job_type=JobType.IPC_DELIVERY.value, token=token)

        def enqueue_newly_ready() -> None:
            for run_id in states:
                if run_id not in outcomes:
                    enqueue_agent_job(run_id)

        def submit_ready(pool: ThreadPoolExecutor) -> None:
            postponed: deque[QueueItem] = deque()
            while queue and len(running) < self.max_workers:
                item = queue.popleft()
                if item.kind == "agent" and item.run_id is not None and item.job_type is not None:
                    queued.discard((item.run_id, item.job_type.value))
                    if item.run_id in outcomes:
                        continue
                if item.kind == "ipc" and item.ipc is not None:
                    queued.discard((item.ipc.request_id, "ipc"))
                    if item.ipc.request_id in ipc_delivered:
                        continue
                needed = resources_for(item)
                blocked = [
                    resource
                    for resource in needed
                    if len(leased.get(resource, [])) >= self.resource_capacity.get(resource, 1)
                ]
                if blocked:
                    self.reporter.event("job_blocked_resource", runtime=self.mode, token=item.token, resources=needed)
                    postponed.append(item)
                    continue
                for resource in needed:
                    leased.setdefault(resource, []).append(item)
                future = pool.submit(run_queue_item, item)
                running[future] = (item, needed)
            queue.extendleft(reversed(postponed))

        def run_queue_item(item: QueueItem) -> JobResult:
            if item.kind == "ipc":
                assert item.ipc is not None
                outcome = outcomes.get(item.ipc.source_run_id)
                message = outcome.message if outcome else ""
                payload = item.ipc.payload_on_success if outcome and outcome.ok else item.ipc.payload_on_failure
                return self.executor.ipc_delivery_job(
                    mode=self.mode,
                    request_id=item.ipc.request_id,
                    source=item.ipc.source_agent,
                    target=item.ipc.target_agent,
                    message=message,
                    payload=payload,
                    request_summary=item.ipc.request_summary,
                )
            assert item.run_id is not None and item.job_type is not None
            state = states[item.run_id]
            spec = state.spec
            state.status = ScheduledRunStatus.RUNNING
            if spec.launch and not state.launched and item.job_type == JobType.OBSERVATION:
                self.executor.launch_agent(spec.agent)
                state.launched = True
            if item.job_type == JobType.OBSERVATION:
                return self.executor.observation_job(agent=spec.agent, phase=spec.phase, step=state.step)
            if item.job_type == JobType.THINKING:
                assert state.last_screenshot is not None
                return self.executor.thinking_job(
                    agent=spec.agent,
                    phase=spec.phase,
                    step=state.step,
                    screenshot=state.last_screenshot,
                    instruction=spec.instruction,
                    memory=memory_from_dependencies(state),
                )
            if item.job_type == JobType.ACTION:
                assert state.last_screenshot is not None and state.pending_action is not None
                return self.executor.action_job(
                    agent=spec.agent,
                    phase=spec.phase,
                    step=state.step,
                    screenshot=state.last_screenshot,
                    action=state.pending_action,
                )
            if item.job_type == JobType.SETTLE_WAIT:
                return self.executor.settle_job(agent=spec.agent, phase=spec.phase, step=state.step)
            raise RuntimeError(f"unsupported item: {item}")

        def finish_agent_job(item: QueueItem, result: JobResult) -> None:
            assert item.run_id is not None and item.job_type is not None
            state = states[item.run_id]
            spec = state.spec
            agent_name = getattr(spec.agent, "name", spec.run_id)
            if not result.ok:
                state.status = ScheduledRunStatus.FAILED
                state.error = result.error
                outcomes[spec.run_id] = ScheduledOutcome(spec.run_id, False, result.error, state.step)
                self.reporter.state_event(
                    agent_name,
                    "FAILED",
                    runtime=self.mode,
                    phase=spec.phase,
                    display_id=getattr(spec.agent, "display_id", None),
                    reason=result.error,
                )
                return
            if item.job_type == JobType.OBSERVATION:
                state.last_screenshot = Path(str(result.output["screenshot"]))
            elif item.job_type == JobType.THINKING:
                action = result.output["action"]
                name = str(action.get("action", "")).lower()
                if name == "complete":
                    state.status = ScheduledRunStatus.DONE
                    state.message = str(action.get("message", "complete"))
                    outcomes[spec.run_id] = ScheduledOutcome(spec.run_id, True, state.message, state.step)
                    self.reporter.state_event(
                        agent_name,
                        "DONE",
                        runtime=self.mode,
                        phase=spec.phase,
                        display_id=getattr(spec.agent, "display_id", None),
                        reason=state.message,
                    )
                    for ipc in ipc_by_source.get(spec.run_id, []):
                        enqueue_ipc(ipc)
                    return
                if name == "fail":
                    state.status = ScheduledRunStatus.FAILED
                    state.message = str(action.get("message", "failed"))
                    outcomes[spec.run_id] = ScheduledOutcome(spec.run_id, False, state.message, state.step)
                    self.reporter.state_event(
                        agent_name,
                        "FAILED",
                        runtime=self.mode,
                        phase=spec.phase,
                        display_id=getattr(spec.agent, "display_id", None),
                        reason=state.message,
                    )
                    return
                state.pending_action = action
            elif item.job_type == JobType.ACTION:
                state.memory += str(result.output.get("memory", ""))
                state.pending_action = None
                state.settle_needed = True
            elif item.job_type == JobType.SETTLE_WAIT:
                state.settle_needed = False
                state.last_screenshot = None
                state.step += 1
            state.status = ScheduledRunStatus.PENDING
            enqueue_agent_job(spec.run_id)

        def finish_ipc_job(item: QueueItem, result: JobResult) -> None:
            assert item.ipc is not None
            if result.ok:
                ipc_delivered.add(item.ipc.request_id)
                ipc_context_by_target.setdefault(item.ipc.target_run_id, []).append(dict(result.output.get("payload", {})))
            enqueue_newly_ready()

        enqueue_newly_ready()
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while queue or running:
                self.reporter.state_event(
                    "runtime",
                    "SCHEDULING",
                    runtime=self.mode,
                    ready_jobs=len(queue),
                    running_jobs=len(running),
                )
                submit_ready(pool)
                if not running:
                    if queue:
                        continue
                    break
                self.reporter.state_event(
                    "runtime",
                    "WAITING",
                    runtime=self.mode,
                    ready_jobs=len(queue),
                    running_jobs=len(running),
                )
                done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
                self.reporter.state_event(
                    "runtime",
                    "SCHEDULING",
                    runtime=self.mode,
                    completed_jobs=len(done),
                    running_jobs=len(running),
                )
                for future in done:
                    item, resources = running.pop(future)
                    for resource in resources:
                        holders = [holder for holder in leased.get(resource, []) if holder.token != item.token]
                        if holders:
                            leased[resource] = holders
                        else:
                            leased.pop(resource, None)
                    result = future.result()
                    if item.kind == "ipc":
                        finish_ipc_job(item, result)
                    else:
                        finish_agent_job(item, result)
                enqueue_newly_ready()

        failed = [outcome for outcome in outcomes.values() if not outcome.ok]
        missing = [run_id for run_id in states if run_id not in outcomes]
        if failed:
            first = failed[0]
            self.reporter.state_event("runtime", "DONE", runtime=self.mode, success=False, reason=first.message)
            return ScheduledResult(False, outcomes, f"{first.run_id}: {first.message}")
        if missing:
            self.reporter.state_event("runtime", "DONE", runtime=self.mode, success=False, reason=f"unfinished runs: {', '.join(missing)}")
            return ScheduledResult(False, outcomes, f"unfinished runs: {', '.join(missing)}")
        self.reporter.state_event("runtime", "DONE", runtime=self.mode, success=True)
        return ScheduledResult(True, outcomes)
