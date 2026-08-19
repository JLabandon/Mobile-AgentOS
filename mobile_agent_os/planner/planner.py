from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..app_agents import AppStaffAgent, SubTask
from ..report import RunReporter
from ..message_layer.messages import RuntimeInformationRequest, RuntimeInformationResponse, RuntimeOperationRequest, RuntimeOperationResponse
from .task_plan import InformationFlow, TaskPlan


class Planner:
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
            information_flows=configured.information_flows or self._flows_from_edges(configured.edges),
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
            "You are the Planner in a mobile app-oriented multi-agent runtime. "
            "Return JSON only. Given a user goal and app-oriented StaffAgent expertise, recruit app agents, "
            "decompose the goal into app-specific subtasks, and produce an information-flow scheduling graph. "
            "Use only facts present in the user goal and app profiles. "
            "Each subtask must be assigned to one available agent. "
            "Use app agents for their own apps only. "
            f"{strategy_text} "
            "Return schema: {\"subtasks\":[{\"agent_name\":\"calendar\",\"instruction\":\"...\","
            "\"max_steps\":12,\"expected_visible_terms\":[\"...\"],\"not_visible_at_finish\":[\"...\"]}],"
            "\"edges\":[[\"gmail\",\"calendar\"]],"
            "\"information_flows\":[{\"from_agent\":\"gmail\",\"to_agent\":\"calendar\",\"name\":\"meeting_details\","
            "\"required\":true,\"delivery\":\"on_source_done\",\"content_contract\":{\"fields\":[\"title\",\"location\",\"notes\"]}}],"
            "\"reason\":\"short\"}."
        )
        app_lines = []
        for agent in self.agents.values():
            config = agent.config
            app_lines.append(
                f"- {config.name}: {config.label}; capabilities: {', '.join(config.capabilities) or 'none'}; "
                f"description: {config.description or 'none'}"
            )
        user = (
            f"User goal:\n{configured.goal}\n\n"
            "Available app-oriented StaffAgents:\n"
            + "\n".join(app_lines)
            + "\n\n"
            "Planning requirements:\n"
            "- Select only agents needed for this goal.\n"
            "- Decompose at app boundaries, not procedure roles such as planner/coder.\n"
            "- Edges should represent scheduling dependency order between app agents.\n"
            "- information_flows should represent planner-declared data handoff: source agent output that should be delivered to a target agent without waiting for a runtime request.\n"
            "- Use runtime REQUEST_INFORMATION only for facts that cannot be anticipated at planning time.\n"
            "- expected_visible_terms must describe final user data or final status text, not transient button labels such as Save, Done, Set Alarm, OK, Create, Submit, or Confirm.\n"
            "- Use enough max_steps for real mobile UI flows. Simple read-only subtasks usually need 4-6; creation flows with pickers, search suggestions, labels, or confirmation dialogs usually need 14-18.\n"
            f"{self._planner_strategy_bullets()}"
            "- Do not include evaluator-only fields, unsupported facts, or device setup assumptions.\n"
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
        information_flows = self._parse_planned_information_flows(parsed, edges)
        plan = TaskPlan(
            task_id=configured.task_id,
            goal=configured.goal,
            mode=self.mode,
            subtasks=subtasks,
            edges=edges,
            information_flows=information_flows,
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

    def _parse_planned_information_flows(self, parsed: dict[str, Any], edges: tuple[tuple[str, str], ...]) -> tuple[InformationFlow, ...]:
        raw_flows = parsed.get("information_flows", [])
        flows: list[InformationFlow] = []
        if isinstance(raw_flows, list):
            for item in raw_flows:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    source = str(item[0]).removesuffix("_agent").strip()
                    target = str(item[1]).removesuffix("_agent").strip()
                    name = "runtime_information"
                    required = True
                    delivery = "on_source_done"
                    fields: tuple[str, ...] = ()
                elif isinstance(item, dict):
                    source = str(item.get("from_agent", item.get("source", ""))).removesuffix("_agent").strip()
                    target = str(item.get("to_agent", item.get("target", ""))).removesuffix("_agent").strip()
                    name = str(item.get("name", "runtime_information")).strip() or "runtime_information"
                    required = bool(item.get("required", True))
                    delivery = str(item.get("delivery", "on_source_done")).strip() or "on_source_done"
                    contract = item.get("content_contract", {})
                    raw_fields = contract.get("fields", []) if isinstance(contract, dict) else item.get("fields", [])
                    fields = tuple(str(field).strip() for field in raw_fields if str(field).strip()) if isinstance(raw_fields, list) else ()
                else:
                    continue
                if source in self.agents and target in self.agents and source != target:
                    flows.append(InformationFlow(source, target, name=name, required=required, delivery=delivery, fields=fields))
        return tuple(flows) or self._flows_from_edges(edges)

    def _flows_from_edges(self, edges: tuple[tuple[str, str], ...]) -> tuple[InformationFlow, ...]:
        return tuple(InformationFlow(from_agent=source, to_agent=target) for source, target in edges)

    def _planner_strategy_name(self) -> str:
        if self._uses_upfront_decomposition():
            return "upfront_parallelism_surface"
        return "runtime_dependency_schedule"

    def _planner_strategy_text(self) -> str:
        if self._uses_upfront_decomposition():
            execution_note = (
                "For steward_serial and mobilerun_steward_serial, the Steward will route extracted result information or operation results along the scheduling graph. "
                if self._is_steward_baseline()
                else "For agentos_parallel and mobilerun_agentos_parallel, the AgentOS runtime will schedule all recruited AppAgents, handle IPC delivery, and route peer information without Steward turn-by-turn forwarding. "
            )
            return (
                "Planning strategy: use MobileSteward-style upfront app decomposition to expose task-level concurrency. "
                "If an information-source app is clearly useful, schedule it as its own top-level subtask so the runtime can run it in parallel with downstream user-visible work. "
                "If an operation-provider app is clearly useful, schedule it as a top-level subtask when its work can progress independently. "
                "For an information dependency, add a provider-to-requester edge and a matching information_flow with a concise content contract. "
                "For an operation dependency, add a provider-to-requester edge when a result should wake the downstream app. "
                f"{execution_note}"
            )
        return "Planning strategy: use MobileSteward-style upfront app decomposition."

    def _planner_strategy_bullets(self) -> str:
        if self._uses_upfront_decomposition():
            runtime_line = (
                "- For steward_serial and mobilerun_steward_serial, downstream subtasks should mention they will use information produced by upstream subtasks, and information_flows should define what to forward.\n"
                if self._is_steward_baseline()
                else "- For agentos_parallel and mobilerun_agentos_parallel, create top-level subtasks for provider and requester apps when both can make independent progress; planner-declared information_flows are delivered on source completion, and runtime requests handle late-bound missing facts.\n"
            )
            return (
                f"- For {self.mode}, it is acceptable to schedule information-source agents as top-level subtasks when the goal clearly benefits from them.\n"
                f"- For {self.mode}, expose possible parallel work at app boundaries instead of forcing one requester app to discover every dependency alone.\n"
                "- Avoid turning providers into procedure roles; each subtask must still be app-specific work performed inside that app.\n"
                "- For every clear provider-to-requester dependency, add information_flows with from_agent, to_agent, name, delivery=on_source_done, and content_contract.fields.\n"
                f"{runtime_line}"
            )
        return "- Decompose the user goal at app boundaries and add edges for information or operation flow.\n"

    def _is_steward_baseline(self) -> bool:
        return self.mode in {"steward", "steward_serial", "mobilerun_steward_serial"}

    def _uses_upfront_decomposition(self) -> bool:
        return self.mode in {"steward", "steward_serial", "agentos_parallel", "mobilerun_steward_serial", "mobilerun_agentos_parallel"}
