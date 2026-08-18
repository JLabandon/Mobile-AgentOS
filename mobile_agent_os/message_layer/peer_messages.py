from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from ..message_layer.messages import (
    RuntimeInformationRequest,
    RuntimeInformationResponse,
    RuntimeOperationRequest,
    RuntimeOperationResponse,
)
from ..kernel.snapshots import ObservationSnapshot, SnapshotStore
from ..planner.task_plan import InformationFlow, TaskPlan


class PeerAgent(Protocol):
    name: str

    def handle_information_request(
        self,
        request: RuntimeInformationRequest,
        out_dir: Path,
        *,
        record_ipc: bool = True,
    ) -> RuntimeInformationResponse:
        ...

    def handle_operation_request(
        self,
        request: RuntimeOperationRequest,
        out_dir: Path,
        *,
        record_ipc: bool = True,
    ) -> RuntimeOperationResponse:
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


class Reporter(Protocol):
    run_dir: Path

    def event(self, kind: str, **payload: object) -> None:
        ...

    def ipc_event(self, **payload: object) -> None:
        ...


class PeerMessageLayer:
    """Runtime IPC boundary for peer request routing and planned flow delivery."""

    def __init__(
        self,
        *,
        agents: dict[str, PeerAgent],
        reporter: Reporter,
        snapshots: SnapshotStore,
        runtime_name: str,
        set_state: Callable[..., None],
        get_state: Callable[[str], str | None],
    ) -> None:
        self.agents = agents
        self.reporter = reporter
        self.snapshots = snapshots
        self.runtime_name = runtime_name
        self.set_state = set_state
        self.get_state = get_state
        self._delivered_edges: set[tuple[str, str, str]] = set()

    def resolve_information_request(
        self,
        request: RuntimeInformationRequest,
        finished: set[str],
    ) -> RuntimeInformationResponse:
        self.reporter.event("runtime_request_routed", runtime=self.runtime_name, via="peer", request=request)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.runtime_name,
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
        self.reporter.event("runtime_response_delivered", runtime=self.runtime_name, via="peer", response=response)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.runtime_name,
            via="peer",
            request_summary=request.need,
            response_summary=response.information,
            evidence=response.evidence,
            policy_decision="not_checked",
        )
        if response.status == "success":
            finished.add(target_agent.name)
            self.set_state(target_agent.name, "DONE", request_id=request.request_id, action="request_handled")
        return response

    def resolve_operation_request(
        self,
        request: RuntimeOperationRequest,
        finished: set[str],
    ) -> RuntimeOperationResponse:
        self.reporter.event("runtime_operation_request_routed", runtime=self.runtime_name, via="peer", request=request)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.runtime_name,
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
        self.reporter.event("runtime_operation_response_delivered", runtime=self.runtime_name, via="peer", response=response)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.runtime_name,
            via="peer",
            request_summary=request.operation,
            response_summary=response.result,
            evidence=response.evidence,
            policy_decision="not_checked",
        )
        if response.status == "success":
            finished.add(target_agent.name)
            self.set_state(target_agent.name, "DONE", request_id=request.request_id, action="operation_handled")
        return response

    def deliver_finished_edge_results(self, plan: TaskPlan, finished: set[str]) -> bool:
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
            evidence_ref = self.reporter.run_dir / f"{source_agent_name}" / f"{snapshot.snapshot_id}_peer_evidence.txt"
            evidence_ref.parent.mkdir(parents=True, exist_ok=True)
            evidence_ref.write_text(self._bounded_snapshot_text(snapshot, max_items=60, max_chars=4000) + "\n", encoding="utf-8")
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
                mode=self.runtime_name,
                via="peer",
                request_summary=request.need,
                evidence_ref=str(evidence_ref),
                policy_decision="not_checked",
            )
            response = self.agents[flow.from_agent].answer_information_from_snapshot(request, snapshot, self.reporter.run_dir)
            self.reporter.event(
                "peer_result_delivered",
                runtime=self.runtime_name,
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
                mode=self.runtime_name,
                via="peer",
                request_summary=request.need,
                response_summary=response.information,
                evidence=response.evidence,
                evidence_ref=str(evidence_ref),
                policy_decision="not_checked",
            )
            self.agents[flow.to_agent].receive_information(response)
            if self.get_state(target_agent_name) == "WAIT_PEER":
                self.set_state(target_agent_name, "READY", request_id=request_id, from_agent=source_agent_name)
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
