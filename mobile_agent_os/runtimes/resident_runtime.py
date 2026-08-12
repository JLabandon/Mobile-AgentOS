from __future__ import annotations

from pathlib import Path

from ..agents import AppStaffAgent, SubTask
from ..ipc import AgentMailbox, IPCLedger
from ..registry import AgentRegistry
from ..report import RunReporter
from ..resident import ForegroundInteraction, ResidentAgentState
from ..runtime_requests import (
    RuntimeInformationRequest,
    RuntimeInformationResponse,
    RuntimeOperationRequest,
    RuntimeOperationResponse,
)
from ..steward import StewardAgent
from ..task_plan import TaskPlan


class ResidentRuntime:
    name = "resident_runtime"

    def __init__(self, agents: dict[str, AppStaffAgent], reporter: RunReporter, task_plans: dict[str, TaskPlan]) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.mailbox = AgentMailbox()
        self.ledger = IPCLedger(reporter, mode=self.name, via="peer")
        self.registry = AgentRegistry(agents, {name: agent.config for name, agent in agents.items()})
        self.foreground = ForegroundInteraction()
        self.last_plan: TaskPlan | None = None
        self.resident: dict[str, ResidentAgentState] = {
            name: ResidentAgentState(
                agent_name=agent.name,
                app_name=agent.config.name,
                capabilities=tuple(agent.config.capabilities),
            )
            for name, agent in agents.items()
        }
        configs = [agent.config for agent in agents.values()]
        for agent in agents.values():
            agent.available_peers = [config for config in configs if config.name != agent.config.name]

    def run(self, task: str, run_dir: Path) -> bool:
        if task not in self.task_plans:
            raise ValueError(f"unsupported task: {task}")
        self.reporter.event("runtime_start", runtime=self.name, task=task, mode="resident")
        self.reporter.event("resident_registry", agents=[state.to_json() for state in self.resident.values()])

        planner = StewardAgent(self.agents, self.reporter, task_plans=self.task_plans, mode="async_single_display")
        plan = planner.plan(task)
        self.last_plan = plan

        active: dict[str, SubTask] = {}
        waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest] = {}
        finished: set[str] = set()
        failed: set[str] = set()

        for subtask in plan.subtasks:
            agent = self.agents[subtask.agent_name]
            agent.begin_task(subtask, run_dir)
            active[subtask.agent_name] = subtask
            state = self.resident[subtask.agent_name]
            state.current_goal = subtask.instruction
            state.set_state("READY")
            self.reporter.event("resident_goal_assigned", agent=agent.name, goal=subtask.instruction)

        max_ticks = sum(getattr(subtask, "max_steps", 6) for subtask in plan.subtasks) + 40
        request_age: dict[str, int] = {}
        for tick in range(1, max_ticks + 1):
            for request in waiting.values():
                request_age[request.request_id] = request_age.get(request.request_id, 0) + 1
            self._trace_scheduler_tick(tick, active, waiting, finished, failed)

            if self._timeout_old_request(waiting, request_age, max_age=20):
                return False

            if self._deliver_ready_responses(waiting):
                continue

            if self._handle_one_request(run_dir):
                continue

            runnable = [name for name in active if name not in waiting and name not in finished and name not in failed]
            if not runnable:
                if len(finished) == len(active):
                    self.reporter.event("runtime_finish", runtime=self.name, task=task, success=True)
                    return True
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="deadlock")
                return False

            agent_name = self._choose_next_agent(runnable, waiting)
            result = self._run_one_step(agent_name)
            if result.status == "waiting" and isinstance(result.request, RuntimeInformationRequest):
                request = self._resolve_information_target(result.request)
                waiting[agent_name] = request
                self.resident[agent_name].pending_requests.append(request.request_id)
                self.resident[agent_name].set_state("WAIT_PEER")
                self.ledger.request_created(request)
                self.mailbox.enqueue_request(request)
                self.ledger.request_queued(request)
                self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeInformationRequest", request=request, lifecycle_status="queued")
            elif result.status == "waiting_operation" and isinstance(result.request, RuntimeOperationRequest):
                request = self._resolve_operation_target(result.request)
                waiting[agent_name] = request
                self.resident[agent_name].pending_requests.append(request.request_id)
                self.resident[agent_name].set_state("WAIT_PEER")
                self.ledger.operation_request_created(request)
                self.mailbox.enqueue_operation_request(request)
                self.ledger.operation_request_queued(request)
                self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeOperationRequest", request=request, lifecycle_status="queued")
            elif result.status == "finished":
                finished.add(agent_name)
                self.resident[agent_name].set_state("DONE")
            elif result.status == "failed":
                failed.add(agent_name)
                self.resident[agent_name].set_state("FAILED")
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason=result.message)
                return False
            else:
                self.resident[agent_name].set_state("READY")

            self._trace_resident_states(tick)
            if len(finished) == len(active):
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=True)
                return True

        self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="max scheduler ticks reached")
        return False

    def _run_one_step(self, agent_name: str):
        agent = self.agents[agent_name]
        state = self.resident[agent_name]
        state.set_state("RUNNING")
        state.yield_status = "running_step"
        self.foreground.update(
            owner_agent=agent.name,
            package=getattr(agent, "package_name", "") or "",
            last_action="step_task",
        )
        self.reporter.event("foreground_interaction", runtime=self.name, foreground_interaction=self.foreground.to_json())
        result = agent.step_task()
        state.yield_status = "yieldable"
        state.owned_resources = ["foreground_interaction"] if self.foreground.owner_agent == agent.name else []
        return result

    def _choose_next_agent(self, runnable: list[str], waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest]) -> str:
        waiting_targets = {request.to_agent.removesuffix("_agent") for request in waiting.values()}
        providers = sorted(name for name in runnable if name in waiting_targets)
        if providers:
            selected = providers[0]
            reason = "provider_unblocks_wait_peer"
        else:
            selected = sorted(runnable)[0]
            reason = "lexicographic_ready_agent"
        self.reporter.event("scheduler_decision", runtime=self.name, selected=f"{selected}_agent", runnable=sorted(runnable), reason=reason)
        return selected

    def _handle_one_request(self, run_dir: Path) -> bool:
        provider_names = sorted(self.agents)
        for name in provider_names:
            agent = self.agents[name]
            message = self.mailbox.dequeue(agent.name)
            if not message:
                continue
            self.resident[name].set_state("RUNNING")
            self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, request=message.payload)
            if message.kind == "RuntimeInformationRequest":
                request = message.payload
                assert isinstance(request, RuntimeInformationRequest)
                self.ledger.request_accepted(request)
                response = agent.handle_information_request(request, run_dir, record_ipc=False)
                self.ledger.response_created(request, response)
                self.mailbox.enqueue_response(response)
                self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeInformationResponse", response=response)
                self.resident[name].set_state("READY")
                return True
            if message.kind == "RuntimeOperationRequest":
                request = message.payload
                assert isinstance(request, RuntimeOperationRequest)
                self.ledger.operation_request_accepted(request)
                response = agent.handle_operation_request(request, run_dir, record_ipc=False)
                self.ledger.operation_response_created(request, response)
                self.mailbox.enqueue_operation_response(response)
                self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeOperationResponse", response=response)
                self.resident[name].set_state("READY")
                return True
            self.mailbox.enqueue_message(message)
        return False

    def _deliver_ready_responses(self, waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest]) -> bool:
        for agent_name, request in list(waiting.items()):
            agent = self.agents[agent_name]
            message = self.mailbox.dequeue(agent.name)
            if not message:
                continue
            if isinstance(request, RuntimeInformationRequest):
                if message.kind != "RuntimeInformationResponse":
                    self.mailbox.enqueue_message(message)
                    continue
                response = message.payload
                assert isinstance(response, RuntimeInformationResponse)
                self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, response=response)
                self.ledger.response_delivered(request, response)
                if response.status != "success":
                    self.reporter.event("error", message=f"runtime information request failed: {response}")
                    return True
                agent.receive_information(response)
            else:
                if message.kind != "RuntimeOperationResponse":
                    self.mailbox.enqueue_message(message)
                    continue
                response = message.payload
                assert isinstance(response, RuntimeOperationResponse)
                self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, response=response)
                self.ledger.operation_response_delivered(request, response)
                agent.receive_operation(response)
            self.resident[agent_name].pending_requests = [
                item for item in self.resident[agent_name].pending_requests if item != request.request_id
            ]
            self.resident[agent_name].set_state("READY")
            del waiting[agent_name]
            return True
        return False

    def _timeout_old_request(
        self,
        waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest],
        request_age: dict[str, int],
        *,
        max_age: int,
    ) -> bool:
        for agent_name, request in waiting.items():
            if request_age.get(request.request_id, 0) <= max_age:
                continue
            if isinstance(request, RuntimeInformationRequest):
                self.ledger.request_timed_out(request)
            else:
                self.ledger.operation_request_timed_out(request)
            self.resident[agent_name].set_state("FAILED")
            self.reporter.event("runtime_finish", runtime=self.name, success=False, reason="request timed out", request=request)
            return True
        return False

    def _resolve_information_target(self, request: RuntimeInformationRequest) -> RuntimeInformationRequest:
        target = self.registry.resolve_capability(need=request.need, preferred_agent=request.to_agent)
        if not target:
            self.ledger.request_rejected(request, reason="no target capability found")
            raise ValueError(f"no target agent for request: {request}")
        if request.to_agent == target.name:
            self.reporter.event("capability_route", runtime=self.name, request_id=request.request_id, selected_agent=target.name, reason="preferred_agent_valid")
            return request
        self.reporter.event("capability_route", runtime=self.name, request_id=request.request_id, selected_agent=target.name, reason="capability_match")
        return RuntimeInformationRequest(
            request_id=request.request_id,
            from_agent=request.from_agent,
            to_agent=target.name,
            need=request.need,
            context=request.context,
            purpose=request.purpose,
            resume_instruction=request.resume_instruction,
            created_at=request.created_at,
        )

    def _resolve_operation_target(self, request: RuntimeOperationRequest) -> RuntimeOperationRequest:
        target = self.registry.resolve_capability(need=request.operation, preferred_agent=request.to_agent)
        if not target:
            self.ledger.operation_request_rejected(request, reason="no target capability found")
            raise ValueError(f"no target agent for operation request: {request}")
        if request.to_agent == target.name:
            self.reporter.event("capability_route", runtime=self.name, request_id=request.request_id, selected_agent=target.name, reason="preferred_agent_valid")
            return request
        self.reporter.event("capability_route", runtime=self.name, request_id=request.request_id, selected_agent=target.name, reason="capability_match")
        return RuntimeOperationRequest(
            request_id=request.request_id,
            from_agent=request.from_agent,
            to_agent=target.name,
            operation=request.operation,
            context=request.context,
            purpose=request.purpose,
            expected_result=request.expected_result,
            resume_instruction=request.resume_instruction,
            created_at=request.created_at,
        )

    def _trace_scheduler_tick(
        self,
        tick: int,
        active: dict[str, SubTask],
        waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest],
        finished: set[str],
        failed: set[str],
    ) -> None:
        self.reporter.event(
            "scheduler_tick",
            runtime=self.name,
            tick=tick,
            active=sorted(active.keys()),
            waiting=sorted(waiting.keys()),
            finished=sorted(finished),
            failed=sorted(failed),
            mailbox_pending=self.mailbox.pending_count(),
            mailbox_summary=self.mailbox.pending_summary(),
            foreground_interaction=self.foreground.to_json(),
            resident_states=[state.to_json() for state in self.resident.values()],
        )

    def _trace_resident_states(self, tick: int) -> None:
        self.reporter.event("resident_state_snapshot", runtime=self.name, tick=tick, agents=[state.to_json() for state in self.resident.values()])
