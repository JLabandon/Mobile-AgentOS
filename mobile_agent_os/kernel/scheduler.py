from __future__ import annotations

import json
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .jobs import JobResult, JobType
from .scheduler_policy import FifoSchedulingPolicy, JobCandidate, ResourceSnapshot, SchedulerPolicy, SchedulerSnapshot


class ScheduledRunStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAIT_PEER = "WAIT_PEER"
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
    service_name: str = ""
    service_instance_index: int = 0
    status: ScheduledRunStatus = ScheduledRunStatus.PENDING
    step: int = 1
    launched: bool = False
    memory: str = ""
    last_screenshot: Path | None = None
    pending_action: dict[str, Any] | None = None
    settle_needed: bool = False
    message: str = ""
    error: str = ""
    waiting_request_id: str = ""


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
    state_step: int = 0
    token: int = field(default=0)


class JobExecutor(Protocol):
    def launch_agent(self, agent: Any) -> None:
        ...

    def create_agent_instance(self, agent: Any, *, service_name: str, run_id: str, instance_index: int) -> Any:
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
        service_agents: dict[str, Any] | None = None,
        service_capacity: dict[str, int] | None = None,
        final_run_id: str = "",
        policy: SchedulerPolicy | None = None,
        duration_estimates: dict[str, float] | None = None,
    ) -> None:
        self.executor = executor
        self.reporter = reporter
        self.mode = mode
        self.max_workers = max_workers
        self.serial_order = serial_order
        self.resource_capacity = resource_capacity or {}
        self.service_agents = service_agents or {}
        self.service_capacity = service_capacity or {}
        self.final_run_id = final_run_id
        self.policy = policy or FifoSchedulingPolicy()
        self.duration_estimates = duration_estimates or {}

    def run(self, *, specs: list[AgentRunSpec], ipc_specs: list[IPCSpec]) -> ScheduledResult:
        self.reporter.state_event(
            "runtime",
            "SCHEDULING",
            runtime=self.mode,
            reason="scheduler_start",
        )
        states: dict[str, AgentRunState] = {}
        for spec in specs:
            state = AgentRunState(spec=spec)
            state.service_name = str(getattr(spec.agent, "name", spec.run_id))
            states[spec.run_id] = state
        service_agents = dict(self.service_agents)
        for spec in specs:
            service_agents.setdefault(str(getattr(spec.agent, "name", spec.run_id)), spec.agent)
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
        persistent_leases: dict[str, list[str]] = {}
        service_owners: dict[str, set[str]] = {}
        service_instance_counters: dict[str, int] = {}
        late_bound_counter = 0
        terminal_success = False
        terminal_reason = ""
        token = 0

        def resource_key(name: str, scope: object = "global") -> str:
            return f"{name}:{scope}"

        def resources_for(item: QueueItem) -> list[str]:
            if item.kind == "ipc":
                return []
            assert item.job_type is not None and item.run_id is not None
            state = states[item.run_id]
            display_id = getattr(state.spec.agent, "display_id", "none")
            if item.job_type == JobType.THINKING:
                return [resource_key("llm_worker", "pool")]
            if display_id == 0 and item.job_type in {JobType.OBSERVATION, JobType.ACTION}:
                resources = [resource_key("foreground_display", "primary")]
            else:
                resources = []
            if item.job_type == JobType.ACTION:
                action = state.pending_action or {}
                if str(action.get("action", "")).lower() in {"input_text", "type_text", "input"}:
                    resources.append(resource_key("ime", "global"))
            if resources:
                return resources
            return []

        def duration_key(item: QueueItem) -> str:
            if item.kind == "ipc":
                return JobType.IPC_DELIVERY.value
            if item.run_id is None or item.job_type is None:
                return "unknown"
            state = states[item.run_id]
            agent_name = str(getattr(state.spec.agent, "name", item.run_id))
            return f"{agent_name}:{item.job_type.value}"

        def estimated_duration(item: QueueItem) -> float:
            return float(
                self.duration_estimates.get(
                    duration_key(item),
                    self.duration_estimates.get(item.job_type.value if item.job_type else item.kind, 1.0),
                )
            )

        def candidate_for(item: QueueItem) -> JobCandidate:
            if item.kind == "ipc" and item.ipc is not None:
                return JobCandidate(
                    token=item.token,
                    kind=item.kind,
                    run_id=item.ipc.request_id,
                    agent_id="runtime",
                    phase="ipc_delivery",
                    job_type=JobType.IPC_DELIVERY.value,
                    resources=tuple(resources_for(item)),
                    estimated_duration_s=estimated_duration(item),
                )
            if item.kind == "agent" and item.run_id is not None and item.job_type is not None:
                state = states[item.run_id]
                return JobCandidate(
                    token=item.token,
                    kind=item.kind,
                    run_id=item.run_id,
                    agent_id=str(getattr(state.spec.agent, "name", item.run_id)),
                    phase=state.spec.phase,
                    job_type=item.job_type.value,
                    step=item.state_step,
                    depends_on=state.spec.depends_on,
                    resources=tuple(resources_for(item)),
                    estimated_duration_s=estimated_duration(item),
                )
            return JobCandidate(token=item.token, kind=item.kind, estimated_duration_s=estimated_duration(item))

        def scheduler_snapshot(items: list[QueueItem]) -> SchedulerSnapshot:
            resource_names = set(self.resource_capacity)
            for item in items:
                resource_names.update(resources_for(item))
            return SchedulerSnapshot(
                candidates=tuple(candidate_for(item) for item in items),
                resources={
                    resource: ResourceSnapshot(
                        name=resource,
                        capacity=self.resource_capacity.get(resource, 1),
                        leased=len(leased.get(resource, [])),
                        persistent_leased=len(persistent_leases.get(resource, [])),
                    )
                    for resource in sorted(resource_names)
                },
                completed_runs=frozenset(outcomes),
                running_jobs=len(running),
                max_workers=self.max_workers,
            )

        def apply_policy_order() -> None:
            if len(queue) < 2:
                return
            items = list(queue)
            by_token = {item.token: item for item in items}
            ordered_candidates = self.policy.order(scheduler_snapshot(items))
            ordered_items = [by_token[candidate.token] for candidate in ordered_candidates if candidate.token in by_token]
            if len(ordered_items) != len(items):
                ordered_items = sorted(items, key=lambda item: item.token)
            queue.clear()
            queue.extend(ordered_items)
            self.reporter.event(
                "scheduler_policy_decision",
                runtime=self.mode,
                policy=self.policy.name,
                ordered_tokens=[item.token for item in ordered_items],
                ordered_jobs=[candidate_for(item).__dict__ for item in ordered_items],
            )

        def persistent_resource_available(resource: str) -> bool:
            return len(persistent_leases.get(resource, [])) < self.resource_capacity.get(resource, 1)

        def acquire_persistent_resource(resource: str, owner: str, reason: str) -> bool:
            if not persistent_resource_available(resource):
                self.reporter.event("resource_blocked", runtime=self.mode, resource=resource, owner=owner, reason=reason)
                return False
            persistent_leases.setdefault(resource, []).append(owner)
            self.reporter.event("resource_acquire", runtime=self.mode, resource=resource, owner=owner, reason=reason)
            return True

        def agent_name_for(run_id: str) -> str:
            state = states[run_id]
            return state.service_name or str(getattr(state.spec.agent, "name", state.spec.run_id))

        def display_name_for(run_id: str) -> str:
            return str(getattr(states[run_id].spec.agent, "name", run_id))

        def service_allows(run_id: str) -> bool:
            state = states[run_id]
            service_name = agent_name_for(run_id)
            owners = service_owners.setdefault(service_name, set())
            if run_id in owners:
                return True
            capacity = max(1, int(self.service_capacity.get(service_name, 1)))
            if len(owners) >= capacity:
                owner = sorted(owners)[0] if owners else ""
                self.reporter.event("scheduler_blocked_service_busy", runtime=self.mode, agent=service_name, run_id=run_id, owner_run_id=owner, capacity=capacity)
                self.reporter.event("service_request_waiting", runtime=self.mode, agent=service_name, run_id=run_id, owner_run_id=owner, capacity=capacity)
                return False
            uses_parallel_instances = service_name in self.service_capacity and capacity > 1
            instance_index = len(owners)
            if uses_parallel_instances:
                instance_index = service_instance_counters.get(service_name, 0)
            if uses_parallel_instances or instance_index > 0:
                slot_resource = resource_key("display_slot", "task_hosting")
                if not acquire_persistent_resource(slot_resource, run_id, "create_app_instance"):
                    self.reporter.event("service_instance_unavailable", runtime=self.mode, agent=service_name, run_id=run_id, reason="display_slot_unavailable")
                    self.reporter.event("service_request_waiting", runtime=self.mode, agent=service_name, run_id=run_id, reason="display_slot_unavailable", capacity=capacity)
                    return False
                factory = getattr(self.executor, "create_agent_instance", None)
                if factory is None:
                    self.reporter.event("service_instance_unavailable", runtime=self.mode, agent=service_name, run_id=run_id, reason="executor_has_no_instance_factory")
                    return False
                try:
                    instance_agent = factory(state.spec.agent, service_name=service_name, run_id=run_id, instance_index=instance_index)
                except Exception as exc:
                    self.reporter.event("service_instance_unavailable", runtime=self.mode, agent=service_name, run_id=run_id, reason=str(exc))
                    return False
                state.spec = replace(state.spec, agent=instance_agent)
                state.service_instance_index = instance_index
                service_instance_counters[service_name] = max(service_instance_counters.get(service_name, 0), instance_index + 1)
                self.reporter.event(
                    "service_instance_created",
                    runtime=self.mode,
                    agent=service_name,
                    run_id=run_id,
                    instance_agent=str(getattr(instance_agent, "name", run_id)),
                    instance_index=instance_index,
                    display_id=getattr(instance_agent, "display_id", None),
                )
            owners.add(run_id)
            self.reporter.event(
                "service_context_started",
                runtime=self.mode,
                agent=service_name,
                run_id=run_id,
                instance_agent=display_name_for(run_id),
                instance_index=state.service_instance_index,
                capacity=capacity,
            )
            return True

        def release_service_if_owner(run_id: str) -> None:
            service_name = agent_name_for(run_id)
            owners = service_owners.get(service_name)
            if owners and run_id in owners:
                owners.remove(run_id)
                if not owners:
                    service_owners.pop(service_name, None)
                self.reporter.event(
                    "service_context_finished",
                    runtime=self.mode,
                    agent=service_name,
                    run_id=run_id,
                    instance_agent=display_name_for(run_id),
                    instance_index=states[run_id].service_instance_index,
                )
            for resource, resource_owners in list(persistent_leases.items()):
                kept = [owner for owner in resource_owners if owner != run_id]
                if len(kept) == len(resource_owners):
                    continue
                if kept:
                    persistent_leases[resource] = kept
                else:
                    persistent_leases.pop(resource, None)
                self.reporter.event("resource_release", runtime=self.mode, resource=resource, owner=run_id, reason="service_context_finished")

        def stop_after_final_completion(run_id: str, evidence: str) -> None:
            nonlocal terminal_success, terminal_reason
            if not self.final_run_id:
                return
            if run_id != self.final_run_id:
                return
            terminal_success = True
            terminal_reason = evidence
            queue.clear()
            self.reporter.event(
                "runtime_final_completion_oracle_matched",
                runtime=self.mode,
                final_run_id=run_id,
                evidence=evidence,
            )
            self.reporter.state_event(
                "runtime",
                "DONE",
                runtime=self.mode,
                success=True,
                reason=evidence,
            )

        def serial_allows(run_id: str) -> bool:
            if not self.serial_order:
                return True
            if run_id not in self.serial_order:
                return True
            for previous in self.serial_order:
                if previous == run_id:
                    return True
                if previous not in outcomes:
                    return False
            return True

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
                completion_check = getattr(self.executor, "completion_check", None)
                evidence = completion_check(state.spec.agent) if callable(completion_check) else None
                if evidence:
                    state.status = ScheduledRunStatus.DONE
                    state.message = evidence
                    outcomes[state.spec.run_id] = ScheduledOutcome(state.spec.run_id, True, state.message, state.spec.max_steps)
                    agent_name = getattr(state.spec.agent, "name", state.spec.run_id)
                    self.reporter.event(
                        "completion_oracle_matched",
                        runtime=self.mode,
                        agent=agent_name,
                        run_id=state.spec.run_id,
                        evidence=evidence,
                        timing="max_steps_boundary",
                    )
                    self.reporter.state_event(
                        agent_name,
                        "DONE",
                        runtime=self.mode,
                        phase=state.spec.phase,
                        display_id=getattr(state.spec.agent, "display_id", None),
                        reason=evidence,
                    )
                    for ipc in ipc_by_source.get(state.spec.run_id, []):
                        enqueue_ipc(ipc)
                    release_service_if_owner(state.spec.run_id)
                    stop_after_final_completion(state.spec.run_id, evidence)
                    return None
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
            if state.status in {ScheduledRunStatus.RUNNING, ScheduledRunStatus.WAIT_PEER, ScheduledRunStatus.DONE, ScheduledRunStatus.FAILED}:
                return
            if not serial_allows(run_id) or not dependencies_satisfied(state) or not service_allows(run_id):
                return
            job_type = next_job_type(state)
            if job_type is None:
                return
            key = (run_id, job_type.value)
            if key in queued:
                return
            state.status = ScheduledRunStatus.READY
            token += 1
            queue.append(QueueItem("agent", run_id=run_id, job_type=job_type, state_step=state.step, token=token))
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
            apply_policy_order()
            while queue and len(running) < self.max_workers:
                item = queue.popleft()
                if item.kind == "agent" and item.run_id is not None and item.job_type is not None:
                    queued.discard((item.run_id, item.job_type.value))
                    if item.run_id in outcomes:
                        continue
                    state = states[item.run_id]
                    current_job_type = next_job_type(state)
                    if state.step != item.state_step or current_job_type != item.job_type:
                        self.reporter.event(
                            "stale_queue_item_discarded",
                            runtime=self.mode,
                            token=item.token,
                            run_id=item.run_id,
                            queued_job_type=item.job_type.value,
                            queued_step=item.state_step,
                            current_job_type=current_job_type.value if current_job_type else "",
                            current_step=state.step,
                        )
                        continue
                if item.kind == "ipc" and item.ipc is not None:
                    queued.discard((item.ipc.request_id, "ipc"))
                    if item.ipc.request_id in ipc_delivered:
                        continue
                needed = resources_for(item)
                blocked = [
                    resource
                    for resource in needed
                    if len(leased.get(resource, [])) + len(persistent_leases.get(resource, [])) >= self.resource_capacity.get(resource, 1)
                ]
                if blocked:
                    self.reporter.event("job_blocked_resource", runtime=self.mode, token=item.token, resources=needed)
                    if item.run_id:
                        self.reporter.state_event(agent_name_for(item.run_id), "WAIT_RESOURCE", runtime=self.mode, run_id=item.run_id, resources=needed)
                    postponed.append(item)
                    continue
                for resource in needed:
                    leased.setdefault(resource, []).append(item)
                if item.kind == "agent" and item.run_id is not None:
                    states[item.run_id].status = ScheduledRunStatus.RUNNING
                future = pool.submit(run_queue_item, item)
                running[future] = (item, needed)
            queue.extendleft(reversed(postponed))

        def target_agent_from_action(action: dict[str, Any]) -> Any | None:
            target = str(action.get("target_agent") or action.get("to_agent") or "").strip()
            if not target:
                return None
            return service_agents.get(target)

        def create_late_bound_request(requester_run_id: str, action: dict[str, Any]) -> None:
            nonlocal late_bound_counter
            requester_state = states[requester_run_id]
            requester_spec = requester_state.spec
            requester_agent = requester_spec.agent
            action_name = str(action.get("action", "")).lower()
            target_agent = target_agent_from_action(action)
            if target_agent is None:
                requester_state.status = ScheduledRunStatus.FAILED
                requester_state.message = f"late-bound request target not found: {action}"
                outcomes[requester_run_id] = ScheduledOutcome(requester_run_id, False, requester_state.message, requester_state.step)
                self.reporter.event("late_bound_request_rejected", runtime=self.mode, run_id=requester_run_id, reason=requester_state.message, action=action)
                return
            late_bound_counter += 1
            request_id = str(action.get("request_id") or f"late_bound_{late_bound_counter:03d}_{requester_run_id}")
            target_name = str(getattr(target_agent, "name", action.get("target_agent", "target_agent")))
            provider_run_id = f"{target_name}_{request_id}"
            need = str(action.get("need") or action.get("operation") or action.get("request") or "")
            resume_instruction = str(action.get("resume_instruction", ""))
            if action_name == "request_operation":
                instruction = (
                    f"Handle a late-bound runtime operation request from {getattr(requester_agent, 'name', requester_run_id)}. "
                    f"Operation: {need}. Expected result: {action.get('expected_result', '')}. "
                    "Complete only after the operation result is visible."
                )
                payload_kind = "RuntimeOperationResponse"
            else:
                instruction = (
                    f"Handle a late-bound runtime information request from {getattr(requester_agent, 'name', requester_run_id)}. "
                    f"Need: {need}. If the requested information is already visible on screen, complete immediately with the exact information and visible evidence. "
                    "The visible note title or app screen does not need to exactly repeat the request wording; semantic field matches are enough. "
                    "Only interact with the app when the current screen does not contain enough evidence."
                )
                payload_kind = "RuntimeInformationResponse"
            provider_state = AgentRunState(
                spec=AgentRunSpec(
                    run_id=provider_run_id,
                    agent=target_agent,
                    instruction=instruction,
                    phase="late_bound_response",
                    max_steps=int(action.get("max_steps", 4)),
                )
            )
            provider_state.service_name = target_name
            states[provider_run_id] = provider_state
            ipc = IPCSpec(
                request_id=request_id,
                source_run_id=provider_run_id,
                target_run_id=requester_run_id,
                source_agent=target_agent,
                target_agent=requester_agent,
                payload_on_success={
                    "kind": payload_kind,
                    "mode": "late_bound_request",
                    "request_id": request_id,
                    "need": need,
                    "resume_instruction": resume_instruction,
                },
                payload_on_failure={
                    "kind": payload_kind,
                    "mode": "late_bound_request",
                    "request_id": request_id,
                    "status": "failed",
                },
                request_summary=need,
            )
            ipc_by_source.setdefault(provider_run_id, []).append(ipc)
            requester_state.status = ScheduledRunStatus.WAIT_PEER
            requester_state.waiting_request_id = request_id
            requester_state.last_screenshot = None
            requester_state.pending_action = None
            requester_state.settle_needed = False
            self.reporter.event(
                "late_bound_request_created",
                runtime=self.mode,
                request_id=request_id,
                request_kind=action_name,
                from_run_id=requester_run_id,
                to_run_id=provider_run_id,
                from_agent=str(getattr(requester_agent, "name", requester_run_id)),
                to_agent=target_name,
                need=need,
            )
            self.reporter.state_event(
                str(getattr(requester_agent, "name", requester_run_id)),
                "WAIT_PEER",
                runtime=self.mode,
                run_id=requester_run_id,
                request_id=request_id,
                to_agent=target_name,
            )
            enqueue_agent_job(provider_run_id)

        def run_queue_item(item: QueueItem) -> JobResult:
            if item.kind == "ipc":
                assert item.ipc is not None
                outcome = outcomes.get(item.ipc.source_run_id)
                message = outcome.message if outcome else ""
                payload = dict(item.ipc.payload_on_success if outcome and outcome.ok else item.ipc.payload_on_failure)
                payload.setdefault("response_message", message)
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
            if (
                spec.launch
                and item.job_type == JobType.OBSERVATION
                and (not state.launched or getattr(spec.agent, "display_id", None) == 0)
            ):
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
                release_service_if_owner(spec.run_id)
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
                    release_service_if_owner(spec.run_id)
                    stop_after_final_completion(spec.run_id, state.message)
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
                    release_service_if_owner(spec.run_id)
                    return
                if name in {"request_information", "request_operation"}:
                    create_late_bound_request(spec.run_id, action)
                    return
                state.pending_action = action
            elif item.job_type == JobType.ACTION:
                state.memory += str(result.output.get("memory", ""))
                state.pending_action = None
                state.settle_needed = True
            elif item.job_type == JobType.SETTLE_WAIT:
                completion_check = getattr(self.executor, "completion_check", None)
                evidence = completion_check(spec.agent) if callable(completion_check) else None
                if evidence:
                    state.status = ScheduledRunStatus.DONE
                    state.message = evidence
                    outcomes[spec.run_id] = ScheduledOutcome(spec.run_id, True, state.message, state.step)
                    self.reporter.event(
                        "completion_oracle_matched",
                        runtime=self.mode,
                        agent=agent_name,
                        run_id=spec.run_id,
                        evidence=evidence,
                    )
                    self.reporter.state_event(
                        agent_name,
                        "DONE",
                        runtime=self.mode,
                        phase=spec.phase,
                        display_id=getattr(spec.agent, "display_id", None),
                        reason=evidence,
                    )
                    for ipc in ipc_by_source.get(spec.run_id, []):
                        enqueue_ipc(ipc)
                    release_service_if_owner(spec.run_id)
                    stop_after_final_completion(spec.run_id, evidence)
                    return
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
                target_state = states.get(item.ipc.target_run_id)
                if target_state and target_state.status == ScheduledRunStatus.WAIT_PEER:
                    target_state.status = ScheduledRunStatus.PENDING
                    target_state.waiting_request_id = ""
                    target_state.last_screenshot = None
                    target_state.pending_action = None
                    target_state.settle_needed = False
                    self.reporter.event("late_bound_response_delivered", runtime=self.mode, request_id=item.ipc.request_id, target_run_id=item.ipc.target_run_id)
                    self.reporter.state_event(
                        str(getattr(target_state.spec.agent, "name", item.ipc.target_run_id)),
                        "READY",
                        runtime=self.mode,
                        run_id=item.ipc.target_run_id,
                        request_id=item.ipc.request_id,
                    )
            enqueue_newly_ready()

        enqueue_newly_ready()
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while (queue or running) and not terminal_success:
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

        if terminal_success:
            return ScheduledResult(True, outcomes, terminal_reason)
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
