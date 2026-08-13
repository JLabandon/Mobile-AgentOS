from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
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
from ..snapshots import ObservationSnapshot, PendingAction, PendingDecision, SnapshotStore, stable_digest
from ..steward import StewardAgent
from ..task_plan import TaskPlan


class SplitPhaseAgent(DisplayBackedAgent, Protocol):
    config: object

    def begin_task(self, subtask: SubTask, out_dir: Path) -> None:
        ...

    def decide_from_snapshot(self, snapshot: ObservationSnapshot, subtask: SubTask, out_dir: Path) -> AgentAction:
        ...

    def receive_information(self, response: object) -> None:
        ...


class MultidisplaySplitPhaseRuntime:
    name = "multidisplay_split_phase"

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
        self._delivered_edges: set[tuple[str, str]] = set()
        self._last_scheduler_signature: tuple[object, ...] | None = None
        self._last_scheduler_trace_at = 0.0
        self._last_idle_signature: tuple[object, ...] | None = None
        self._last_idle_trace_at = 0.0

    def run(self, task: str, run_dir: Path) -> bool:
        if task not in self.task_plans:
            raise ValueError(f"unsupported task: {task}")
        self.reporter.event("runtime_start", runtime=self.name, task=task, mode="multidisplay_split_phase")
        plan = self._plan(task)
        self.last_plan = plan
        active = {subtask.agent_name: subtask for subtask in plan.subtasks}
        finished: set[str] = set()
        failed: set[str] = set()

        for agent_name, subtask in active.items():
            agent = self.agents[agent_name]
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

                progressed = self._run_one_ready_action(run_dir, finished, failed)
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

                if self._start_one_observe_and_think(active, finished, plan):
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
        ]
        if not candidates:
            return False
        name = candidates[0]
        agent = self.agents[name]
        subtask = active[name]
        slot = self.display_manager.slot_for_agent(agent.name)
        observe_resource = display_observation_resource(slot.display_id)
        self.resources.acquire(agent.name, [observe_resource], reason="observe")
        self._set_state(agent.name, "OBSERVING", display_id=slot.display_id)
        try:
            snapshot = self.display_manager.observe(agent)
            self.snapshots.put(snapshot)
            self._snapshot_by_agent[agent.name] = snapshot
            self.reporter.event(
                "snapshot_created",
                runtime=self.name,
                agent=agent.name,
                snapshot_id=snapshot.snapshot_id,
                display_id=snapshot.display_id,
                app_package=snapshot.app_package,
                ui_text_digest=snapshot.ui_text_digest,
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
            target
            for source, target in plan.edges
            if f"{source}_agent" not in finished
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

    def _run_one_ready_action(self, run_dir: Path, finished: set[str], failed: set[str]) -> bool:
        ready = sorted(self._pending_action_by_agent)
        if not ready:
            return False
        agent_name = ready[0]
        pending = self._pending_action_by_agent[agent_name]
        agent_key = agent_name.removesuffix("_agent")
        agent = self.agents[agent_key]
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
        self._set_state(agent.name, "ACTING", display_id=slot.display_id, action=pending.action.action)
        try:
            result = self.display_manager.input(agent, pending.action)
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
        else:
            self._set_state(agent.name, "READY")
        return True

    def _deliver_finished_edge_results(self, plan: TaskPlan, finished: set[str]) -> bool:
        delivered = False
        for source, target in plan.edges:
            edge = (source, target)
            if edge in self._delivered_edges:
                continue
            source_agent_name = f"{source}_agent"
            target_agent_name = f"{target}_agent"
            if source_agent_name not in finished or target not in self.agents:
                continue
            snapshot = self.snapshots.latest_for_agent(source_agent_name)
            if not snapshot or not snapshot.visible_text.strip():
                continue
            target_subtask = next((subtask for subtask in plan.subtasks if subtask.agent_name == target), None)
            peer_payload = self._peer_result_summary(source_agent_name, snapshot, target_instruction=target_subtask.instruction if target_subtask else "")
            evidence_ref = str(self.reporter.run_dir / f"{source_agent_name}" / f"{snapshot.snapshot_id}_peer_evidence.txt")
            Path(evidence_ref).parent.mkdir(parents=True, exist_ok=True)
            Path(evidence_ref).write_text(self._bounded_snapshot_text(snapshot, max_items=60, max_chars=4000) + "\n", encoding="utf-8")
            request_id = f"peer_result_{source}_{target}"
            response = RuntimeInformationResponse(
                request_id=request_id,
                from_agent=source_agent_name,
                to_agent=target_agent_name,
                status="success",
                information=peer_payload,
                source_app=getattr(self.agents[source].config, "label", source),
                confidence="medium",
                evidence=peer_payload,
                limitations="Delivered by AgentOS runtime from a completed peer agent snapshot along a planner edge.",
            )
            self.reporter.event(
                "peer_result_delivered",
                runtime=self.name,
                source_agent=source_agent_name,
                target_agent=target_agent_name,
                request_id=request_id,
                via="peer",
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
                response_summary=response.information,
                evidence="Provider app finished and exposed relevant UI evidence.",
                evidence_ref=evidence_ref,
            )
            self.agents[target].receive_information(response)
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

    def _peer_result_summary(self, source_agent_name: str, snapshot: ObservationSnapshot, *, target_instruction: str = "") -> str:
        visible_lines: list[str] = []
        for event in reversed(self.reporter.events):
            if event.get("agent") != source_agent_name:
                continue
            event_lines = [str(item).strip() for item in event.get("visible_texts", []) if str(item).strip()]
            if event_lines:
                visible_lines = event_lines
                break
        visible_lines = visible_lines or [line.strip() for line in self._bounded_snapshot_text(snapshot, max_items=30, max_chars=1600).splitlines() if line.strip()]
        focused = self._select_relevant_lines(visible_lines, target_instruction)
        if focused:
            return "\n".join(focused)[:800]
        for event in reversed(self.reporter.events):
            if event.get("kind") != "agent_step" or event.get("agent") != source_agent_name:
                continue
            action = event.get("action")
            if isinstance(action, dict):
                for key in ("information", "result", "reason"):
                    value = str(action.get(key) or "").strip()
                    if value:
                        return value[:500]
            reason = str(event.get("reason") or "").strip()
            if reason:
                return reason[:500]
        return self._bounded_snapshot_text(snapshot, max_items=8, max_chars=500)

    def _select_relevant_lines(self, lines: list[str], target_instruction: str) -> list[str]:
        instruction_tokens = {
            token
            for token in target_instruction.lower().replace("_", " ").split()
            if len(token) >= 4
        }
        signal_words = {
            "agenda",
            "address",
            "date",
            "details",
            "email",
            "end",
            "location",
            "meeting",
            "notes",
            "place",
            "start",
            "subject",
            "time",
            "title",
        }
        scored: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            lowered = line.lower()
            tokens = set(lowered.replace(":", " ").replace(",", " ").split())
            score = len(tokens & signal_words) * 3 + len(tokens & instruction_tokens)
            if score > 0:
                scored.append((score, index, line))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected: list[str] = []
        for _, _, line in scored:
            if line not in selected:
                selected.append(line)
            if len(selected) >= 6:
                break
        return selected

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
