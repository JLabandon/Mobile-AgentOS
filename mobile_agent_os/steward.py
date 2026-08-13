from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents import AppStaffAgent, SubTask
from .report import RunReporter
from .runtime_requests import RuntimeInformationRequest, RuntimeInformationResponse, RuntimeOperationRequest, RuntimeOperationResponse
from .task_plan import TaskPlan


class StewardAgent:
    def __init__(
        self,
        agents: dict[str, AppStaffAgent],
        reporter: RunReporter,
        task_plans: dict[str, TaskPlan],
        mode: str = "steward",
    ) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.mode = mode
        configs = [agent.config for agent in agents.values()]
        for agent in agents.values():
            agent.available_peers = [config for config in configs if config.name != agent.config.name]

    def plan(self, task: str) -> TaskPlan:
        if task not in self.task_plans:
            raise ValueError(f"unsupported task: {task}")
        configured = self.task_plans[task]
        if not configured.subtasks:
            return self._dynamic_plan(configured)
        plan = TaskPlan(
            task_id=configured.task_id,
            goal=configured.goal,
            mode=self.mode,
            subtasks=configured.subtasks,
            edges=configured.edges,
            success_criteria=configured.success_criteria,
            environment=configured.environment,
        )
        self.reporter.event(
            "steward_plan",
            message=" -> ".join(subtask.agent_name for subtask in plan.subtasks),
            task_plan=plan,
        )
        self.reporter.event("task_plan_created", task_plan=plan)
        return plan

    def _dynamic_plan(self, configured: TaskPlan) -> TaskPlan:
        prompt_dir = self.reporter.run_dir / "steward_agent" / "plan" / self.mode / configured.task_id
        prompt_dir.mkdir(parents=True, exist_ok=True)
        strategy_text = self._planner_strategy_text()
        system = (
            "You are the central StewardAgent in a MobileSteward-style mobile multi-agent system. "
            "Return JSON only. Given a user goal and app-oriented StaffAgent expertise, recruit app agents, "
            "decompose the goal into app-specific subtasks, and produce an information-flow scheduling graph. "
            "Do not assume hidden benchmark criteria; use only the user goal and app profiles. "
            "Each subtask must be assigned to one available agent. "
            "Use app agents for their own apps only. "
            f"{strategy_text} "
            "Return schema: {\"subtasks\":[{\"agent_name\":\"calendar\",\"instruction\":\"...\","
            "\"max_steps\":12,\"expected_visible_terms\":[\"...\"],\"not_visible_at_finish\":[\"...\"]}],"
            "\"edges\":[[\"calendar\",\"gmail\"]],\"reason\":\"short\"}."
        )
        app_lines = []
        for agent in self.agents.values():
            config = agent.config
            guidelines = "; ".join(config.task_guidelines) if config.task_guidelines else "none"
            app_lines.append(
                f"- {config.name}: {config.label}; capabilities: {', '.join(config.capabilities) or 'none'}; "
                f"description: {config.description or 'none'}; expertise_memory: {guidelines}"
            )
        user = (
            f"User goal:\n{configured.goal}\n\n"
            "Available app-oriented StaffAgents:\n"
            + "\n".join(app_lines)
            + "\n\n"
            "Planning requirements:\n"
            "- Select only agents needed for this goal.\n"
            "- Decompose at app boundaries, not procedure roles such as planner/coder.\n"
            "- Edges should represent possible information or operation flow between app agents.\n"
            "- expected_visible_terms must describe final user data or final status text, not transient button labels such as Save, Done, Set Alarm, OK, Create, Submit, or Confirm.\n"
            "- Use enough max_steps for real mobile UI flows. Simple read-only subtasks usually need 4-6; creation flows with pickers, search suggestions, labels, or confirmation dialogs usually need 14-18.\n"
            f"{self._planner_strategy_bullets()}"
            "- Do not include benchmark-only fields, hidden answers, or emulator setup assumptions.\n"
        )
        prompt_path = prompt_dir / "llm_prompt.json"
        response_path = prompt_dir / "llm_response.txt"
        prompt_path.write_text(json.dumps({"system": system, "user": user}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        llm = next(iter(self.agents.values())).llm
        try:
            raw_content = llm.raw_chat(system=system, user=user, max_tokens=900)
        except Exception as exc:
            self.reporter.event(
                "model_call_failed",
                agent="steward_agent",
                step=0,
                attempt=1,
                prompt=str(prompt_path),
                message=str(exc),
            )
            raise
        response_path.write_text(raw_content, encoding="utf-8", errors="replace")
        self.reporter.event(
            "model_call",
            agent="steward_agent",
            step=0,
            attempt=1,
            prompt=str(prompt_path),
            response=str(response_path),
            raw_response=raw_content,
        )
        parsed = llm.parse_json_content(raw_content)
        subtasks = self._parse_planned_subtasks(parsed)
        edges = self._parse_planned_edges(parsed)
        plan = TaskPlan(
            task_id=configured.task_id,
            goal=configured.goal,
            mode=self.mode,
            subtasks=subtasks,
            edges=edges,
            success_criteria=configured.success_criteria,
            environment=configured.environment,
        )
        self.reporter.event(
            "steward_plan",
            message=" -> ".join(subtask.agent_name for subtask in plan.subtasks),
            task_plan=plan,
            reason=parsed.get("reason", ""),
            planner_strategy=self._planner_strategy_name(),
        )
        self.reporter.event("task_plan_created", task_plan=plan)
        return plan

    def _parse_planned_subtasks(self, parsed: dict[str, Any]) -> tuple[SubTask, ...]:
        raw_subtasks = parsed.get("subtasks")
        if not isinstance(raw_subtasks, list) or not raw_subtasks:
            raise ValueError("steward plan requires non-empty subtasks")
        subtasks: list[SubTask] = []
        for item in raw_subtasks:
            if not isinstance(item, dict):
                raise ValueError(f"bad planned subtask: {item}")
            agent_name = str(item.get("agent_name", "")).removesuffix("_agent").strip()
            if agent_name not in self.agents:
                raise ValueError(f"planned unknown agent: {agent_name}")
            instruction = str(item.get("instruction", "")).strip()
            if not instruction:
                raise ValueError(f"planned subtask for {agent_name} missing instruction")
            expected = item.get("expected_visible_terms", [])
            not_visible = item.get("not_visible_at_finish", [])
            if not isinstance(expected, list):
                expected = []
            if not isinstance(not_visible, list):
                not_visible = []
            subtasks.append(
                SubTask(
                    agent_name=agent_name,
                    instruction=instruction,
                    max_steps=self._normalize_max_steps(item.get("max_steps", 12)),
                    required_terms=tuple(self._filter_expected_terms(expected)),
                    forbidden_terms=tuple(self._filter_expected_terms(not_visible)),
                    launch_args=tuple(str(arg) for arg in item.get("launch_args", []) if str(arg).strip())
                    if isinstance(item.get("launch_args", []), list)
                    else (),
                )
            )
        return tuple(subtasks)

    def _normalize_max_steps(self, raw_value: Any) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = 12
        return min(max(value, 4), 20)

    def _filter_expected_terms(self, expected: list[Any]) -> list[str]:
        control_terms = {
            "save",
            "done",
            "ok",
            "create",
            "submit",
            "confirm",
            "authorize",
            "pay",
            "set alarm",
            "alarm set",
            "alarm time",
            "payment required",
            "payment authorization",
            "completed",
            "confirmed",
            "successful",
            "event saved",
            "saved",
            "title",
            "date",
            "time",
            "start time",
            "end time",
            "location",
            "agenda",
            "notes",
            "description",
        }
        filtered: list[str] = []
        for term in expected:
            value = str(term).strip()
            if value and value.lower() not in control_terms:
                filtered.append(value)
        return filtered

    def _parse_planned_edges(self, parsed: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        raw_edges = parsed.get("edges", [])
        if not isinstance(raw_edges, list):
            return ()
        edges: list[tuple[str, str]] = []
        for edge in raw_edges:
            if not isinstance(edge, (list, tuple)) or len(edge) != 2:
                continue
            source = str(edge[0]).removesuffix("_agent").strip()
            target = str(edge[1]).removesuffix("_agent").strip()
            if source in self.agents and target in self.agents and source != target:
                edges.append((source, target))
        return tuple(edges)

    def _planner_strategy_name(self) -> str:
        if self._uses_upfront_decomposition():
            return "upfront_parallelism_surface"
        return "runtime_dependency_schedule"

    def _planner_strategy_text(self) -> str:
        if self._uses_upfront_decomposition():
            execution_note = (
                "For steward_serial, the Steward will route extracted result information or operation results along the scheduling graph. "
                if self._is_steward_baseline()
                else "For multidisplay_split_phase, the AgentOS runtime will schedule all recruited AppAgents, handle IPC delivery, and route peer information without Steward turn-by-turn forwarding. "
            )
            return (
                "Planning strategy: use MobileSteward-style upfront app decomposition to expose task-level concurrency. "
                "If an information-source app is clearly useful, schedule it as its own top-level subtask so the runtime can run it in parallel with downstream user-visible work. "
                "If an operation-provider app is clearly useful, schedule it as a top-level subtask when its work can progress independently. "
                "For an information dependency, add a provider-to-requester edge. "
                "For an operation dependency, add a provider-to-requester edge when a result should wake the downstream app. "
                f"{execution_note}"
            )
        return "Planning strategy: use MobileSteward-style upfront app decomposition."

    def _planner_strategy_bullets(self) -> str:
        if self._uses_upfront_decomposition():
            runtime_line = (
                "- For steward_serial, downstream subtasks should mention they will use information produced by upstream subtasks.\n"
                if self._is_steward_baseline()
                else "- For multidisplay_split_phase, create top-level subtasks for provider and requester apps when both can make independent progress; runtime IPC will deliver whichever information becomes available first.\n"
            )
            return (
                f"- For {self.mode}, it is acceptable to schedule information-source agents as top-level subtasks when the goal clearly benefits from them.\n"
                f"- For {self.mode}, expose possible parallel work at app boundaries instead of forcing one requester app to discover every dependency alone.\n"
                "- Avoid turning providers into procedure roles; each subtask must still be app-specific work performed inside that app.\n"
                f"{runtime_line}"
            )
        return "- Decompose the user goal at app boundaries and add edges for information or operation flow.\n"

    def _is_steward_baseline(self) -> bool:
        return self.mode in {"steward", "steward_serial"}

    def _uses_upfront_decomposition(self) -> bool:
        return self.mode in {"steward", "steward_serial", "multidisplay_split_phase"}

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

    def run(self, task: str, run_dir: Path) -> bool:
        plan = self.plan(task)
        return self.run_plan(plan, run_dir)

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
        for source, target in plan.edges:
            if source != subtask.agent_name or target not in self.agents:
                continue
            information, evidence = self._extract_finished_subtask_result(plan, subtask, target)
            if not information:
                continue
            request_id = f"steward_result_{source}_{target}"
            response = RuntimeInformationResponse(
                request_id=request_id,
                from_agent=source_agent_name,
                to_agent=f"{target}_agent",
                status="success",
                information=information,
                source_app=self.agents[source].config.label,
                confidence="medium",
                evidence=evidence or information,
                limitations="Extracted by Steward from the upstream agent's latest visible UI text.",
            )
            self.reporter.event(
                "steward_extract_result",
                source_agent=source_agent_name,
                target_agent=f"{target}_agent",
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
            self.agents[target].receive_information(response)

    def _extract_finished_subtask_result(self, plan: TaskPlan, subtask: SubTask, target: str) -> tuple[str, str]:
        source_agent_name = f"{subtask.agent_name}_agent"
        visible = self._latest_visible_information(source_agent_name)
        if not visible:
            return "", ""
        prompt_dir = self.reporter.run_dir / "steward_agent" / "extract" / plan.task_id / f"{subtask.agent_name}_to_{target}"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        target_instruction = ""
        for item in plan.subtasks:
            if item.agent_name == target:
                target_instruction = item.instruction
                break
        system = (
            "You are a StewardAgent extracting concise handoff information between app-oriented agents. "
            "Return JSON only. Extract only information that is directly relevant to the downstream subtask. "
            "Relevant means the target agent must use the fact to complete its own UI task. "
            "Do not include navigation labels, account text, unrelated notes, or generic UI chrome."
        )
        user = (
            f"Overall user goal:\n{plan.goal}\n\n"
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
                    target_agent=f"{target}_agent",
                    reason="extractor response missing information field",
                )
                return visible, "raw visible UI text fallback"
            return information, evidence
        except Exception as exc:
            self.reporter.event("steward_extract_failed", source_agent=source_agent_name, target_agent=f"{target}_agent", reason=str(exc))
            return visible, "raw visible UI text fallback"

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
