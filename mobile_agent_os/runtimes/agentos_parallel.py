from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from ..actions import AgentAction
from ..agents import SubTask
from ..display import ActionResult, DisplayBackedAgent, DisplayManager, DisplaySlot
from ..guards import ActionGuard, risk_level_for_action
from ..report import RunReporter
from ..resources import (
    ResourceManager,
    ResourceSpec,
    display_input_resource,
    display_observation_resource,
    display_slot_resource,
)
from ..runtime_requests import RuntimeInformationRequest
from ..runtime_requests import RuntimeInformationResponse
from ..runtime_requests import RuntimeOperationRequest
from ..runtime_requests import RuntimeOperationResponse
from ..snapshots import ObservationSnapshot, PendingAction, PendingDecision, SnapshotStore, stable_digest
from ..steward import StewardAgent
from ..task_plan import InformationFlow, TaskPlan


class SplitPhaseAgent(DisplayBackedAgent, Protocol):
    config: object

    def begin_task(self, subtask: SubTask, out_dir: Path) -> None:
        ...

    def decide_from_snapshot(self, snapshot: ObservationSnapshot, subtask: SubTask, out_dir: Path) -> AgentAction:
        ...

    def answer_information_from_snapshot(
        self,
        request: RuntimeInformationRequest,
        snapshot: ObservationSnapshot,
        out_dir: Path,
    ) -> RuntimeInformationResponse:
        ...

    def receive_information(self, response: object) -> None:
        ...

    def receive_operation(self, response: object) -> None:
        ...

    def handle_information_request(self, request: RuntimeInformationRequest, out_dir: Path, *, record_ipc: bool = True) -> RuntimeInformationResponse:
        ...

    def handle_operation_request(self, request: RuntimeOperationRequest, out_dir: Path, *, record_ipc: bool = True) -> RuntimeOperationResponse:
        ...


class AgentOSParallelRuntime:
    name = "agentos_parallel"

    def __init__(
        self,
        agents: dict[str, SplitPhaseAgent],
        reporter: RunReporter,
        task_plans: dict[str, TaskPlan],
        *,
        display_manager: DisplayManager | None = None,
        max_workers: int = 2,
    ) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.display_manager = display_manager or DisplayManager()
        self.snapshots = SnapshotStore()
        self.guard = ActionGuard()
        self.resources = ResourceManager(
            [
                ResourceSpec("llm_worker", capacity=max_workers),
                ResourceSpec("ime", capacity=1),
            ],
            reporter=reporter,
        )
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.last_plan: TaskPlan | None = None
        self._states: dict[str, str] = {}
        self._future_by_agent: dict[str, Future[AgentAction]] = {}
        self._decision_by_agent: dict[str, PendingDecision] = {}
        self._snapshot_by_agent: dict[str, ObservationSnapshot] = {}
        self._pending_action_by_agent: dict[str, PendingAction] = {}
        self._action_count_by_agent: dict[str, int] = {}
        self._delivered_edges: set[tuple[str, str]] = set()
        self._last_scheduler_signature: tuple[object, ...] | None = None
        self._last_scheduler_trace_at = 0.0
        self._last_idle_signature: tuple[object, ...] | None = None
        self._last_idle_trace_at = 0.0

    def run(self, task: str, run_dir: Path) -> bool:
        if task not in self.task_plans:
            raise ValueError(f"unsupported task: {task}")
        self.reporter.event("runtime_start", runtime=self.name, task=task, mode="agentos_parallel")
        plan = self._plan(task)
        self.last_plan = plan
        active = {subtask.agent_name: subtask for subtask in plan.subtasks}
        finished: set[str] = set()
        failed: set[str] = set()

        for agent_name, subtask in active.items():
            agent = self.agents[agent_name]
            self._action_count_by_agent[agent.name] = 0
            package = agent.display_package()
            slot = self.display_manager.allocate(agent.name, package)
            if slot.observation_channel != "foreground_uiautomator":
                self.resources.acquire(agent.name, [display_slot_resource(slot.display_id)], reason="resident_display_slot")
            agent.begin_task(subtask, run_dir)
            self._set_state(agent.name, "READY", display_id=slot.display_id, task=subtask.instruction)
            self.reporter.event(
                "display_slot_allocated",
                runtime=self.name,
                agent=agent.name,
                display_id=slot.display_id,
                app_package=package,
                observation_channel=slot.observation_channel,
            )

        deadline = time.monotonic() + max(180.0, sum(subtask.max_steps for subtask in active.values()) * 25.0)
        tick = 0
        try:
            while time.monotonic() < deadline:
                tick += 1
                failed.update(self._collect_completed_decisions())
                self._trace_scheduler_tick(tick)

                progressed = self._run_one_ready_action(active, run_dir, finished, failed)
                if self._deliver_finished_edge_results(plan, finished):
                    progressed = True
                if failed:
                    self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="agent failed")
                    return False
                if len(finished) == len(active):
                    self.reporter.event("runtime_finish", runtime=self.name, task=task, success=True)
                    return True
                if progressed:
                    continue

                if self._start_ready_observe_and_think(active, finished, plan):
                    continue

                if self._future_by_agent:
                    self._trace_scheduler_idle("waiting_for_llm_decision", sorted(self._future_by_agent))
                    time.sleep(0.01)
                    continue

                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="deadlock")
                return False
        finally:
            self.executor.shutdown(wait=True)
            for agent in self.agents.values():
                self.resources.release_agent(agent.name, reason="runtime_shutdown")

        self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="scheduler deadline reached")
        return False

    def _start_ready_observe_and_think(self, active: dict[str, SubTask], finished: set[str], plan: TaskPlan) -> bool:
        started_agents: list[str] = []
        shared_foreground = self._shared_foreground_observation()
        while self._start_one_observe_and_think(active, finished, plan):
            agent_name = next(reversed(self._future_by_agent))
            if agent_name not in started_agents:
                started_agents.append(agent_name)
            if shared_foreground:
                break
        if len(started_agents) > 1:
            self.reporter.event(
                "parallel_thinking_batch_submitted",
                runtime=self.name,
                agents=started_agents,
                count=len(started_agents),
            )
        return bool(started_agents)

    def _plan(self, task: str) -> TaskPlan:
        configured = self.task_plans[task]
        if configured.subtasks:
            self.reporter.event("steward_plan", message=" -> ".join(subtask.agent_name for subtask in configured.subtasks), task_plan=configured)
            return configured
        planner = StewardAgent(cast(object, self.agents), self.reporter, self.task_plans, mode=self.name)
        return planner.plan(task)

    def _start_one_observe_and_think(self, active: dict[str, SubTask], finished: set[str], plan: TaskPlan) -> bool:
        shared_foreground = self._shared_foreground_observation()
        if shared_foreground and (self._future_by_agent or self._pending_action_by_agent):
            return False
        candidates = [
            name
            for name in self._ordered_candidates(active, finished, plan, shared_foreground=shared_foreground)
            if f"{name}_agent" not in finished
            and self._states.get(f"{name}_agent") == "READY"
            and f"{name}_agent" not in self._future_by_agent
            and f"{name}_agent" not in self._pending_action_by_agent
            and self._action_count_by_agent.get(f"{name}_agent", 0) < active[name].max_steps
        ]
        if not candidates:
            return False
        name = candidates[0]
        agent = self.agents[name]
        subtask = active[name]
        slot = self.display_manager.slot_for_agent(agent.name)
        observe_resource = display_observation_resource(slot.display_id)
        self.resources.acquire(agent.name, [observe_resource], reason="observe")
        try:
            self._switch_display(agent, purpose="observe", display_id=slot.display_id)
            self._set_state(agent.name, "OBSERVING", display_id=slot.display_id)
            snapshot = self.display_manager.capture_observation(agent)
            self.snapshots.put(snapshot)
            self._snapshot_by_agent[agent.name] = snapshot
            self.reporter.event(
                "snapshot_created",
                runtime=self.name,
                agent=agent.name,
                snapshot_id=snapshot.snapshot_id,
                requested_display_id=slot.display_id,
                display_id=snapshot.display_id,
                app_package=snapshot.app_package,
                ui_text_digest=snapshot.ui_text_digest,
            )
            if snapshot.display_id != slot.display_id:
                self.reporter.event(
                    "observation_surface_remapped",
                    runtime=self.name,
                    agent=agent.name,
                    requested_display_id=slot.display_id,
                    actual_display_id=snapshot.display_id,
                    app_package=snapshot.app_package,
                )
        finally:
            self.resources.release_agent(agent.name, reason="observe_complete")

        decision = PendingDecision(
            decision_id=f"dec_{uuid.uuid4().hex[:10]}",
            agent=agent.name,
            task_id=subtask.agent_name,
            snapshot_id=snapshot.snapshot_id,
            submitted_at=time.monotonic(),
            prompt_hash=stable_digest(snapshot.visible_text),
        )
        self._decision_by_agent[agent.name] = decision
        self.resources.acquire(agent.name, ["llm_worker"], reason="llm_decide")
        future = self.executor.submit(agent.decide_from_snapshot, snapshot, subtask, self.reporter.run_dir)
        self._future_by_agent[agent.name] = future
        self._set_state(agent.name, "THINKING", display_id=slot.display_id, decision_id=decision.decision_id, snapshot_id=snapshot.snapshot_id)
        self.reporter.event(
            "llm_submitted",
            runtime=self.name,
            agent=agent.name,
            decision_id=decision.decision_id,
            snapshot_id=snapshot.snapshot_id,
            display_id=slot.display_id,
            actual_display_id=snapshot.display_id,
        )
        return True

    def _ordered_candidates(
        self,
        active: dict[str, SubTask],
        finished: set[str],
        plan: TaskPlan,
        *,
        shared_foreground: bool,
    ) -> list[str]:
        order = {subtask.agent_name: index for index, subtask in enumerate(plan.subtasks)}
        names = sorted(active, key=lambda name: order.get(name, len(order)))
        if not shared_foreground:
            return names
        blocked_targets = {
            flow.to_agent
            for flow in self._plan_information_flows(plan)
            if f"{flow.from_agent}_agent" not in finished
        }
        unblocked = [name for name in names if name not in blocked_targets]
        blocked = [name for name in names if name in blocked_targets]
        return unblocked + blocked

    def _collect_completed_decisions(self) -> set[str]:
        failed_agents: set[str] = set()
        for agent_name, future in list(self._future_by_agent.items()):
            if not future.done():
                continue
            self.resources.release_agent(agent_name, reason="llm_decide_complete")
            decision = self._decision_by_agent[agent_name]
            snapshot = self._snapshot_by_agent[agent_name]
            try:
                action = future.result()
            except Exception as exc:
                self._set_state(agent_name, "FAILED", message=str(exc))
                self.reporter.event("llm_completed", runtime=self.name, agent=agent_name, decision_id=decision.decision_id, status="failed", message=str(exc))
                del self._future_by_agent[agent_name]
                failed_agents.add(agent_name)
                continue
            pending = PendingAction(
                action_id=f"act_{uuid.uuid4().hex[:10]}",
                decision_id=decision.decision_id,
                agent=agent_name,
                action=action,
                snapshot_id=snapshot.snapshot_id,
                risk_level=risk_level_for_action(action),
                target_ref={"target_id": action.target_id, "target_text": action.target_text},
            )
            self._pending_action_by_agent[agent_name] = pending
            self._set_state(agent_name, "READY_TO_ACT", decision_id=decision.decision_id, action=action.action)
            self.reporter.event(
                "llm_completed",
                runtime=self.name,
                agent=agent_name,
                decision_id=decision.decision_id,
                status="success",
                action=action.to_json(),
            )
            self.reporter.event(
                "ready_to_act",
                runtime=self.name,
                agent=agent_name,
                decision_id=decision.decision_id,
                action=action.to_json(),
                risk_level=pending.risk_level,
            )
            del self._future_by_agent[agent_name]
        return failed_agents

    def _run_one_ready_action(self, active: dict[str, SubTask], run_dir: Path, finished: set[str], failed: set[str]) -> bool:
        ready = sorted(self._pending_action_by_agent)
        if not ready:
            return False
        agent_name = ready[0]
        pending = self._pending_action_by_agent[agent_name]
        agent_key = agent_name.removesuffix("_agent")
        agent = self.agents[agent_key]
        subtask = active[agent_key]
        current_steps = self._action_count_by_agent.get(agent.name, 0)
        if current_steps >= subtask.max_steps:
            del self._pending_action_by_agent[agent_name]
            failed.add(agent.name)
            self._set_state(agent.name, "FAILED", message=f"max steps reached ({subtask.max_steps})")
            self.reporter.event(
                "agent_finish",
                runtime=self.name,
                agent=agent.name,
                message=f"max steps reached ({subtask.max_steps})",
                max_steps=subtask.max_steps,
            )
            return True
        slot = self.display_manager.slot_for_agent(agent.name)
        snapshot = self.snapshots.get(pending.snapshot_id)
        guard = self.guard.check(pending, snapshot, slot)
        self.reporter.event(
            "action_guard",
            runtime=self.name,
            agent=agent.name,
            action_id=pending.action_id,
            mode=guard.mode,
            result="pass" if guard.passed else "fail",
            reason=guard.reason,
            snapshot_age_ms=guard.snapshot_age_ms,
        )
        if not guard.passed:
            del self._pending_action_by_agent[agent_name]
            self._set_state(agent.name, "READY", reason=f"guard_failed:{guard.reason}")
            self.reporter.event("reobserve_required", runtime=self.name, agent=agent.name, reason=guard.reason)
            return True

        resources = self._resources_for_action(slot, pending.action)
        ok, reason = self.resources.can_acquire(agent.name, resources)
        if not ok:
            self._set_state(agent.name, "WAIT_RESOURCE", resources=resources, reason=reason)
            self.reporter.event("resource_blocked", runtime=self.name, agent=agent.name, resources=resources, reason=reason)
            return False
        self.resources.acquire(agent.name, resources, reason=f"action:{pending.action.action}")
        try:
            self._switch_display(agent, purpose="act", display_id=slot.display_id, action=pending.action.action)
            self._set_state(agent.name, "ACTING", display_id=slot.display_id, action=pending.action.action)
            result = self.display_manager.apply_input(agent, pending.action)
            self.reporter.event(
                "agent_step",
                runtime=self.name,
                agent=agent.name,
                action=pending.action.to_json(),
                status=result.status,
                reason=pending.action.reason,
                display_id=slot.display_id,
                snapshot_id=snapshot.snapshot_id,
            )
        except Exception as exc:
            result = ActionResult(status="failed", message=str(exc))
            self.reporter.event("agent_step", runtime=self.name, agent=agent.name, action=pending.action.to_json(), status="failed", reason=str(exc))
        finally:
            self.resources.release_agent(agent.name, reason="action_complete")
            del self._pending_action_by_agent[agent_name]

        self._action_count_by_agent[agent.name] = current_steps + 1
        if result.status == "finished" or pending.action.action == "FINISH":
            finished.add(agent.name)
            self._set_state(agent.name, "DONE")
        elif result.status == "failed":
            failed.add(agent.name)
            self._set_state(agent.name, "FAILED", message=result.message)
        elif pending.action.action == "REQUEST_INFORMATION":
            request = RuntimeInformationRequest.create(
                from_agent=agent.name,
                to_agent=pending.action.to_agent or "",
                need=pending.action.need or "",
                context=pending.action.context or "",
                purpose=pending.action.purpose or "",
                resume_instruction=pending.action.resume_instruction or "",
            )
            self.reporter.event("runtime_request_created", runtime=self.name, request=request)
            self._set_state(agent.name, "WAIT_PEER", request_id=request.request_id)
            response = self._resolve_information_request(request, finished)
            if response.status != "success":
                failed.add(agent.name)
                self._set_state(agent.name, "FAILED", request_id=request.request_id, message=response.limitations or "runtime information request failed")
            else:
                agent.receive_information(response)
                self._set_state(agent.name, "READY", request_id=request.request_id, from_agent=response.from_agent)
        elif pending.action.action == "REQUEST_OPERATION":
            request = RuntimeOperationRequest.create(
                from_agent=agent.name,
                to_agent=pending.action.to_agent or "",
                operation=pending.action.operation or "",
                context=pending.action.context or "",
                purpose=pending.action.purpose or "",
                expected_result=pending.action.expected_result or "",
                resume_instruction=pending.action.resume_instruction or "",
            )
            self.reporter.event("runtime_operation_request_created", runtime=self.name, request=request)
            self._set_state(agent.name, "WAIT_PEER", request_id=request.request_id)
            response = self._resolve_operation_request(request, finished)
            if response.status != "success":
                failed.add(agent.name)
                self._set_state(agent.name, "FAILED", request_id=request.request_id, message=response.limitations or "runtime operation request failed")
            else:
                agent.receive_operation(response)
                self._set_state(agent.name, "READY", request_id=request.request_id, from_agent=response.from_agent)
        else:
            if self._action_count_by_agent[agent.name] >= subtask.max_steps:
                failed.add(agent.name)
                self._set_state(agent.name, "FAILED", message=f"max steps reached ({subtask.max_steps})")
                self.reporter.event(
                    "agent_finish",
                    runtime=self.name,
                    agent=agent.name,
                    message=f"max steps reached ({subtask.max_steps})",
                    max_steps=subtask.max_steps,
                )
            else:
                self._set_state(agent.name, "READY")
        return True

    def _resolve_information_request(self, request: RuntimeInformationRequest, finished: set[str]) -> RuntimeInformationResponse:
        self.reporter.event("runtime_request_routed", runtime=self.name, via="peer", request=request)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.name,
            via="peer",
            request_summary=request.need,
            policy_decision="not_checked",
        )
        target_key = request.to_agent.removesuffix("_agent")
        if target_key not in self.agents:
            return RuntimeInformationResponse(
                request_id=request.request_id,
                from_agent=request.to_agent,
                to_agent=request.from_agent,
                status="failed",
                information="",
                source_app=request.to_agent,
                confidence="low",
                limitations=f"target agent not found: {request.to_agent}",
            )
        target_agent = self.agents[target_key]
        response = target_agent.handle_information_request(request, self.reporter.run_dir, record_ipc=True)
        self.reporter.event("runtime_response_delivered", runtime=self.name, via="peer", response=response)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.name,
            via="peer",
            request_summary=request.need,
            response_summary=response.information,
            evidence=response.evidence,
            policy_decision="not_checked",
        )
        if response.status == "success":
            finished.add(target_agent.name)
            self._set_state(target_agent.name, "DONE", request_id=request.request_id, action="request_handled")
        return response

    def _resolve_operation_request(self, request: RuntimeOperationRequest, finished: set[str]) -> RuntimeOperationResponse:
        self.reporter.event("runtime_operation_request_routed", runtime=self.name, via="peer", request=request)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.name,
            via="peer",
            request_summary=request.operation,
            policy_decision="not_checked",
        )
        target_key = request.to_agent.removesuffix("_agent")
        if target_key not in self.agents:
            return RuntimeOperationResponse(
                request_id=request.request_id,
                from_agent=request.to_agent,
                to_agent=request.from_agent,
                status="failed",
                result="",
                source_app=request.to_agent,
                limitations=f"target agent not found: {request.to_agent}",
            )
        target_agent = self.agents[target_key]
        response = target_agent.handle_operation_request(request, self.reporter.run_dir, record_ipc=True)
        self.reporter.event("runtime_operation_response_delivered", runtime=self.name, via="peer", response=response)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.name,
            via="peer",
            request_summary=request.operation,
            response_summary=response.result,
            evidence=response.evidence,
            policy_decision="not_checked",
        )
        if response.status == "success":
            finished.add(target_agent.name)
            self._set_state(target_agent.name, "DONE", request_id=request.request_id, action="operation_handled")
        return response

    def _switch_display(self, agent: SplitPhaseAgent, *, purpose: str, display_id: int, action: str | None = None) -> None:
        started = time.monotonic()
        slot, switched = self.display_manager.activate_for_observation(agent) if purpose == "observe" else self.display_manager.activate_for_input(agent)
        elapsed = round(time.monotonic() - started, 3)
        if not switched:
            return
        self.reporter.state_event(
            agent.name,
            "SWITCH",
            t=round(time.monotonic() - self.reporter.started_monotonic - elapsed, 3),
            runtime=self.name,
            display_id=display_id,
            purpose=purpose,
            action=action,
        )
        self.reporter.event(
            "display_switch",
            runtime=self.name,
            agent=agent.name,
            display_id=slot.display_id,
            purpose=purpose,
            action=action,
            elapsed=elapsed,
        )

    def _deliver_finished_edge_results(self, plan: TaskPlan, finished: set[str]) -> bool:
        delivered = False
        for flow in self._plan_information_flows(plan):
            edge = (flow.from_agent, flow.to_agent, flow.name)
            if edge in self._delivered_edges:
                continue
            source_agent_name = f"{flow.from_agent}_agent"
            target_agent_name = f"{flow.to_agent}_agent"
            if source_agent_name not in finished or flow.to_agent not in self.agents:
                continue
            snapshot = self.snapshots.latest_for_agent(source_agent_name)
            if not snapshot or not snapshot.visible_text.strip():
                continue
            evidence_ref = str(self.reporter.run_dir / f"{source_agent_name}" / f"{snapshot.snapshot_id}_peer_evidence.txt")
            Path(evidence_ref).parent.mkdir(parents=True, exist_ok=True)
            Path(evidence_ref).write_text(self._bounded_snapshot_text(snapshot, max_items=60, max_chars=4000) + "\n", encoding="utf-8")
            request_id = f"planner_flow_{flow.name}_{flow.from_agent}_{flow.to_agent}"
            request = RuntimeInformationRequest(
                request_id=request_id,
                from_agent=target_agent_name,
                to_agent=source_agent_name,
                need=f"{flow.name}: {', '.join(flow.fields) or 'planner-declared information'}",
                context=f"Planner-declared information flow from {flow.from_agent} to {flow.to_agent}.",
                purpose="Provide information needed by the downstream app agent.",
                resume_instruction="Use the returned information to continue the assigned app task.",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationRequest",
                status="created",
                from_agent=request.from_agent,
                to_agent=request.to_agent,
                mode=self.name,
                via="peer",
                request_summary=request.need,
                evidence_ref=evidence_ref,
                policy_decision="not_checked",
            )
            response = self.agents[flow.from_agent].answer_information_from_snapshot(request, snapshot, self.reporter.run_dir)
            self.reporter.event(
                "peer_result_delivered",
                runtime=self.name,
                source_agent=source_agent_name,
                target_agent=target_agent_name,
                request_id=request_id,
                via="peer",
                flow=flow,
                snapshot_id=snapshot.snapshot_id,
            )
            self.reporter.ipc_event(
                request_id=request_id,
                message_kind="RuntimeInformationResponse",
                status="delivered",
                from_agent=response.from_agent,
                to_agent=response.to_agent,
                mode=self.name,
                via="peer",
                request_summary=f"{flow.name}: {', '.join(flow.fields) or 'planner-declared information'}",
                response_summary=response.information,
                evidence=response.evidence,
                evidence_ref=evidence_ref,
                policy_decision="not_checked",
            )
            self.agents[flow.to_agent].receive_information(response)
            if self._states.get(target_agent_name) == "WAIT_PEER":
                self._set_state(target_agent_name, "READY", request_id=request_id, from_agent=source_agent_name)
            self._delivered_edges.add(edge)
            delivered = True
        return delivered

    def _bounded_snapshot_text(self, snapshot: ObservationSnapshot, *, max_items: int = 30, max_chars: int = 1600) -> str:
        values: list[str] = []
        for node in snapshot.target_nodes:
            for key in ("text", "content_desc"):
                value = str(node.get(key, "")).strip()
                if value and value not in values:
                    values.append(value)
            if len(values) >= max_items:
                break
        text = "\n".join(values) or snapshot.visible_text
        return text[:max_chars]

    def _plan_information_flows(self, plan: TaskPlan) -> tuple[InformationFlow, ...]:
        if plan.information_flows:
            return plan.information_flows
        return tuple(InformationFlow(from_agent=source, to_agent=target) for source, target in plan.edges)

    def _resources_for_action(self, slot: DisplaySlot, action: AgentAction) -> list[str]:
        resources = [display_input_resource(slot.display_id), f"app_session:{slot.owner_agent}"]
        if action.action == "input":
            resources.append("ime")
        return resources

    def _shared_foreground_observation(self) -> bool:
        slots = self.display_manager.list_slots()
        if not slots:
            return False
        display_ids = {slot.display_id for slot in slots}
        foreground_slots = [slot for slot in slots if slot.observation_channel == "foreground_uiautomator"]
        return len(display_ids) == 1 and len(foreground_slots) > 1

    def _set_state(self, agent: str, state: str, **payload: object) -> None:
        self._states[agent] = state
        self.reporter.state_event(agent, state, runtime=self.name, **payload)

    def _trace_scheduler_tick(self, tick: int) -> None:
        states = dict(sorted(self._states.items()))
        pending_decisions = sorted(self._future_by_agent)
        ready_actions = sorted(self._pending_action_by_agent)
        resources = self.resources.snapshot()
        displays = [slot.__dict__ for slot in self.display_manager.list_slots()]
        signature = (
            tuple(states.items()),
            tuple(pending_decisions),
            tuple(ready_actions),
            tuple(sorted((name, len(leases)) for name, leases in resources.items())),
            tuple((slot["display_id"], slot["owner_agent"], slot["status"]) for slot in displays),
        )
        now = time.monotonic()
        if signature == self._last_scheduler_signature and now - self._last_scheduler_trace_at < 1.0:
            return
        self._last_scheduler_signature = signature
        self._last_scheduler_trace_at = now
        self.reporter.event(
            "scheduler_tick",
            runtime=self.name,
            tick=tick,
            states=states,
            pending_decisions=pending_decisions,
            ready_actions=ready_actions,
            resources=resources,
            displays=displays,
        )

    def _trace_scheduler_idle(self, reason: str, pending_agents: list[str]) -> None:
        signature = (reason, tuple(pending_agents))
        now = time.monotonic()
        if signature == self._last_idle_signature and now - self._last_idle_trace_at < 1.0:
            return
        self._last_idle_signature = signature
        self._last_idle_trace_at = now
        self.reporter.event(
            "scheduler_idle",
            runtime=self.name,
            reason=reason,
            pending_agents=pending_agents,
        )
