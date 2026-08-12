from __future__ import annotations

from pathlib import Path

from ..agents import AppStaffAgent
from ..steward import StewardAgent
from ..ipc import AgentMailbox, IPCLedger
from ..report import RunReporter
from ..runtime_requests import (
    RuntimeInformationRequest,
    RuntimeInformationResponse,
    RuntimeOperationRequest,
    RuntimeOperationResponse,
)
from ..task_plan import TaskPlan


class AsyncSingleDisplayRuntime:
    name = "async_single_display"

    def __init__(self, agents: dict[str, AppStaffAgent], reporter: RunReporter, task_plans: dict[str, TaskPlan]) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.mailbox = AgentMailbox()
        self.ledger = IPCLedger(reporter, mode=self.name, via="peer")
        self.last_plan: TaskPlan | None = None
        configs = [agent.config for agent in agents.values()]
        for agent in agents.values():
            agent.available_peers = [config for config in configs if config.name != agent.config.name]

    def run(self, task: str, run_dir: Path) -> bool:
        if task not in self.task_plans:
            raise ValueError(f"unsupported task: {task}")
        self.reporter.event("runtime_start", runtime=self.name, task=task)
        planner = StewardAgent(self.agents, self.reporter, task_plans=self.task_plans, mode=self.name)
        plan = planner.plan(task)
        self.last_plan = plan

        active: dict[str, object] = {}
        waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest] = {}
        finished: set[str] = set()
        failed: set[str] = set()

        for subtask in plan.subtasks:
            agent = self.agents[subtask.agent_name]
            agent.begin_task(subtask, run_dir)
            active[subtask.agent_name] = subtask

        max_ticks = sum(getattr(subtask, "max_steps", 6) for subtask in plan.subtasks) + 30
        for tick in range(1, max_ticks + 1):
            self.reporter.event(
                "scheduler_tick",
                runtime=self.name,
                tick=tick,
                active=sorted(active.keys()),
                waiting=sorted(waiting.keys()),
                finished=sorted(finished),
                mailbox_pending=self.mailbox.pending_count(),
            )

            delivered = self._deliver_ready_responses(waiting)
            if delivered:
                continue

            request_handled = self._handle_one_request(run_dir)
            if request_handled:
                continue

            runnable = [name for name in active if name not in waiting and name not in finished and name not in failed]
            if not runnable:
                if len(finished) == len(active):
                    self.reporter.event("runtime_finish", runtime=self.name, task=task, success=True)
                    return True
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="deadlock")
                return False

            agent_name = self._choose_next_agent(runnable)
            agent = self.agents[agent_name]
            result = agent.step_task()
            if result.status == "waiting" and isinstance(result.request, RuntimeInformationRequest):
                request = result.request
                waiting[agent_name] = request
                self.ledger.request_created(request)
                self.mailbox.enqueue_request(request)
                self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeInformationRequest", request=request)
                self.ledger.request_routed(request)
            elif result.status == "waiting_operation" and isinstance(result.request, RuntimeOperationRequest):
                request = result.request
                waiting[agent_name] = request
                self.ledger.operation_request_created(request)
                self.mailbox.enqueue_operation_request(request)
                self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeOperationRequest", request=request)
                self.ledger.operation_request_routed(request)
            elif result.status == "finished":
                finished.add(agent_name)
            elif result.status == "failed":
                failed.add(agent_name)
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason=result.message)
                return False

            if len(finished) == len(active):
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=True)
                return True

        self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason="max scheduler ticks reached")
        return False

    def _choose_next_agent(self, runnable: list[str]) -> str:
        return sorted(runnable)[0]

    def _handle_one_request(self, run_dir: Path) -> bool:
        for agent in self.agents.values():
            message = self.mailbox.dequeue(agent.name)
            if not message:
                continue
            if message.kind != "RuntimeInformationRequest":
                if message.kind == "RuntimeOperationRequest":
                    request = message.payload
                    assert isinstance(request, RuntimeOperationRequest)
                    self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, request=request)
                    self.ledger.operation_request_received(request)
                    response = agent.handle_operation_request(request, run_dir, record_ipc=False)
                    self.ledger.operation_response_created(request, response)
                    self.mailbox.enqueue_operation_response(response)
                    self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeOperationResponse", response=response)
                    return True
                self.mailbox.enqueue_response(message.payload)  # type: ignore[arg-type]
                continue
            request = message.payload
            assert isinstance(request, RuntimeInformationRequest)
            self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, request=request)
            self.ledger.request_received(request)
            response = agent.handle_information_request(request, run_dir, record_ipc=False)
            self.ledger.response_created(request, response)
            self.mailbox.enqueue_response(response)
            self.reporter.event("mailbox_enqueue", runtime=self.name, message_kind="RuntimeInformationResponse", response=response)
            return True
        return False

    def _deliver_ready_responses(self, waiting: dict[str, RuntimeInformationRequest | RuntimeOperationRequest]) -> bool:
        for agent_name, request in list(waiting.items()):
            agent = self.agents[agent_name]
            message = self.mailbox.dequeue(agent.name)
            if not message:
                continue
            if isinstance(request, RuntimeInformationRequest):
                if message.kind != "RuntimeInformationResponse":
                    if message.kind == "RuntimeOperationResponse":
                        self.mailbox.enqueue_operation_response(message.payload)  # type: ignore[arg-type]
                    else:
                        self.mailbox.enqueue_request(message.payload)  # type: ignore[arg-type]
                    continue
                response = message.payload
                assert isinstance(response, RuntimeInformationResponse)
                self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, response=response)
                self.ledger.response_delivered(request, response)
                if response.status != "success":
                    self.reporter.event("error", message=f"runtime information request failed: {response}")
                    return True
                agent.receive_information(response)
                del waiting[agent_name]
                return True
            if message.kind != "RuntimeOperationResponse":
                if message.kind == "RuntimeInformationResponse":
                    self.mailbox.enqueue_response(message.payload)  # type: ignore[arg-type]
                else:
                    self.mailbox.enqueue_operation_request(message.payload)  # type: ignore[arg-type]
                continue
            response = message.payload
            assert isinstance(response, RuntimeOperationResponse)
            self.reporter.event("mailbox_dequeue", runtime=self.name, agent=agent.name, message_kind=message.kind, response=response)
            self.ledger.operation_response_delivered(request, response)
            agent.receive_operation(response)
            del waiting[agent_name]
            return True
        return False
