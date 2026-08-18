from __future__ import annotations

import json
from pathlib import Path

from ..app_agents import AppStaffAgent, SubTask
from ..message_layer.messages import (
    RuntimeInformationRequest,
    RuntimeInformationResponse,
    RuntimeOperationRequest,
    RuntimeOperationResponse,
)
from ..planner.task_plan import InformationFlow, TaskPlan
from ..report import RunReporter


class StewardController:
    """Serial baseline controller for MobileSteward-style execution.

    Planner produces the app-level graph. This controller executes that graph
    sequentially and performs steward-mediated request routing for the baseline.
    """

    def __init__(
        self,
        agents: dict[str, AppStaffAgent],
        reporter: RunReporter,
        *,
        mode: str = "steward",
    ) -> None:
        self.agents = agents
        self.reporter = reporter
        self.mode = mode

    def resolve_information_request(self, request: RuntimeInformationRequest, run_dir: Path) -> RuntimeInformationResponse:
        if self.mode == "steward":
            self.reporter.event("runtime_request_routed", mode=self.mode, via="steward", request=request)
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationRequest",
                status="routed",
                from_agent=request.from_agent,
                to_agent=request.to_agent,
                mode=self.mode,
                via="steward",
                request_summary=request.need,
            )
        else:
            self.reporter.event("runtime_request_routed", mode=self.mode, via="peer", request=request)
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationRequest",
                status="routed",
                from_agent=request.from_agent,
                to_agent=request.to_agent,
                mode=self.mode,
                via="peer",
                request_summary=request.need,
            )
        target_agent = self.agents[request.to_agent.removesuffix("_agent")]
        response = target_agent.handle_information_request(request, run_dir)
        if self.mode == "steward":
            self.reporter.event("runtime_response_delivered", mode=self.mode, via="steward", response=response)
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationResponse",
                status="delivered",
                from_agent=response.from_agent,
                to_agent=response.to_agent,
                mode=self.mode,
                via="steward",
                request_summary=request.need,
                response_summary=response.information,
                evidence=response.evidence,
            )
        else:
            self.reporter.event("runtime_response_delivered", mode=self.mode, via="peer", response=response)
            self.reporter.ipc_event(
                request_id=request.request_id,
                message_kind="RuntimeInformationResponse",
                status="delivered",
                from_agent=response.from_agent,
                to_agent=response.to_agent,
                mode=self.mode,
                via="peer",
                request_summary=request.need,
                response_summary=response.information,
                evidence=response.evidence,
            )
        return response

    def resolve_operation_request(self, request: RuntimeOperationRequest, run_dir: Path) -> RuntimeOperationResponse:
        via = "steward" if self.mode == "steward" else "peer"
        self.reporter.event("runtime_operation_request_routed", mode=self.mode, via=via, request=request)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=via,
            request_summary=request.operation,
            policy_decision="not_checked",
        )
        target_agent = self.agents[request.to_agent.removesuffix("_agent")]
        response = target_agent.handle_operation_request(request, run_dir)
        self.reporter.event("runtime_operation_response_delivered", mode=self.mode, via=via, response=response)
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.mode,
            via=via,
            request_summary=request.operation,
            response_summary=response.result,
            evidence=response.evidence,
            policy_decision="not_checked",
        )
        return response

    def _is_steward_baseline(self) -> bool:
        return self.mode in {"steward", "steward_serial", "mobilerun_steward_serial"}

    def run_plan(self, plan: TaskPlan, run_dir: Path) -> bool:
        handled_provider_subtasks: set[str] = set()
        for subtask in plan.subtasks:
            if self._is_steward_baseline() and subtask.agent_name in handled_provider_subtasks:
                self.reporter.event(
                    "steward_skip_duplicate_provider",
                    agent=f"{subtask.agent_name}_agent",
                    instruction=subtask.instruction,
                    reason="provider already handled an incoming runtime request in this plan",
                )
                continue
            agent = self.agents[subtask.agent_name]
            result = agent.run(subtask, run_dir)
            while result.status in {"waiting", "waiting_operation"} and result.request:
                if isinstance(result.request, RuntimeInformationRequest):
                    response = self.resolve_information_request(result.request, run_dir)
                    if response.status != "success":
                        self.reporter.event("error", message=f"runtime information request failed: {response}")
                        return False
                    agent.receive_information(response)
                elif isinstance(result.request, RuntimeOperationRequest):
                    handled_provider_subtasks.add(result.request.to_agent.removesuffix("_agent"))
                    response = self.resolve_operation_request(result.request, run_dir)
                    agent.receive_operation(response)
                else:
                    self.reporter.event("error", message=f"unexpected request: {result.request}")
                    return False
                result = agent.run(subtask, run_dir)
            if result.status != "finished":
                self.reporter.event("error", message=f"{subtask.agent_name} failed")
                return False
            if self._is_steward_baseline():
                self._forward_finished_subtask_result(plan, subtask)
        return True

    def _forward_finished_subtask_result(self, plan: TaskPlan, subtask: SubTask) -> None:
        source_agent_name = f"{subtask.agent_name}_agent"
        for flow in self._plan_information_flows(plan):
            if flow.from_agent != subtask.agent_name or flow.to_agent not in self.agents:
                continue
            information, evidence = self._extract_finished_subtask_result(plan, subtask, flow)
            if not information:
                continue
            request_id = f"steward_flow_{flow.name}_{flow.from_agent}_{flow.to_agent}"
            response = RuntimeInformationResponse(
                request_id=request_id,
                from_agent=source_agent_name,
                to_agent=f"{flow.to_agent}_agent",
                status="success",
                information=information,
                source_app=self.agents[flow.from_agent].config.label,
                confidence="medium",
                evidence=evidence or information,
                limitations="Extracted by Steward from the upstream agent's latest visible UI text.",
            )
            self.reporter.event(
                "steward_extract_result",
                source_agent=source_agent_name,
                target_agent=f"{flow.to_agent}_agent",
                flow=flow,
                information=information,
                evidence=evidence,
            )
            self.reporter.ipc_event(
                request_id=request_id,
                message_kind="RuntimeInformationResponse",
                status="delivered",
                from_agent=response.from_agent,
                to_agent=response.to_agent,
                mode=self.mode,
                via="steward",
                response_summary=response.information,
                evidence=response.evidence,
            )
            self.agents[flow.to_agent].receive_information(response)

    def _extract_finished_subtask_result(self, plan: TaskPlan, subtask: SubTask, flow: InformationFlow) -> tuple[str, str]:
        source_agent_name = f"{subtask.agent_name}_agent"
        visible = self._latest_visible_information(source_agent_name)
        if not visible:
            return "", ""
        prompt_dir = self.reporter.run_dir / "steward_agent" / "extract" / plan.task_id / f"{subtask.agent_name}_to_{flow.to_agent}"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        target_instruction = ""
        for item in plan.subtasks:
            if item.agent_name == flow.to_agent:
                target_instruction = item.instruction
                break
        system = (
            "You are the Planner extracting concise handoff information between app-oriented agents. "
            "Return JSON only. Extract only information that is directly relevant to the downstream subtask. "
            "Relevant means the target agent must use the fact to complete its own UI task. "
            "Do not include navigation labels, account text, unrelated notes, or generic UI chrome."
        )
        user = (
            f"Overall user goal:\n{plan.goal}\n\n"
            f"Planner-declared information flow:\nname={flow.name}; fields={', '.join(flow.fields) or 'unspecified'}; required={flow.required}; delivery={flow.delivery}\n\n"
            f"Source subtask:\n{subtask.instruction}\n\n"
            f"Target subtask:\n{target_instruction}\n\n"
            f"Visible source-app UI text:\n{visible}\n\n"
            "Return schema: {\"information\":\"concise extracted facts\", \"evidence\":\"short supporting visible text\"}. "
            "Use semicolon-separated key-value facts such as \"Title: ...; Location: ...\". "
            "Do not include context facts that the target does not need to type, select, decide from, or verify. "
            "For email apps, do not treat inbox timestamps, sender rows, or message received dates as meeting dates unless the email content explicitly says they are the event date or time. "
            "Omit absent fields instead of writing negative facts like 'not specified'. "
            "If no relevant information is visible, return empty strings."
        )
        prompt_path = prompt_dir / "llm_prompt.json"
        response_path = prompt_dir / "llm_response.txt"
        prompt_path.write_text(json.dumps({"system": system, "user": user}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        llm = self.agents[subtask.agent_name].llm
        try:
            raw = llm.raw_chat(system=system, user=user, max_tokens=300)
            response_path.write_text(raw, encoding="utf-8", errors="replace")
            self.reporter.event(
                "model_call",
                agent="steward_agent",
                step=0,
                attempt=1,
                prompt=str(prompt_path),
                response=str(response_path),
                raw_response=raw,
                purpose="extract_finished_subtask_result",
            )
            parsed = llm.parse_json_content(raw)
            information = str(parsed.get("information", "")).strip()
            evidence = str(parsed.get("evidence", "")).strip()
            if not information and "information" not in parsed:
                self.reporter.event(
                    "steward_extract_schema_fallback",
                    source_agent=source_agent_name,
                    target_agent=f"{flow.to_agent}_agent",
                    reason="extractor response missing information field",
                )
                return visible, "raw visible UI text fallback"
            return information, evidence
        except Exception as exc:
            self.reporter.event("steward_extract_failed", source_agent=source_agent_name, target_agent=f"{flow.to_agent}_agent", reason=str(exc))
            return visible, "raw visible UI text fallback"

    def _plan_information_flows(self, plan: TaskPlan) -> tuple[InformationFlow, ...]:
        return plan.information_flows or self._flows_from_edges(plan.edges)

    def _flows_from_edges(self, edges: tuple[tuple[str, str], ...]) -> tuple[InformationFlow, ...]:
        return tuple(InformationFlow(from_agent=source, to_agent=target) for source, target in edges)

    def _latest_visible_information(self, agent_name: str) -> str:
        for event in reversed(self.reporter.events):
            if event.get("kind") not in {"agent_step", "post_action_completion_check", "completion_check"} or event.get("agent") != agent_name:
                continue
            texts = event.get("visible_texts")
            if not isinstance(texts, list):
                continue
            cleaned = [str(text).strip() for text in texts if str(text).strip()]
            if cleaned:
                return "\n".join(cleaned[:30])
        return ""
