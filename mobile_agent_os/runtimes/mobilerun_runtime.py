from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from ..agents import AppStaffAgent
from ..mobilerun_executor import MobileRunExecutor
from ..report import RunReporter
from ..steward import StewardAgent
from ..task_plan import TaskPlan


class _MobileRunRuntimeBase:
    name = "mobilerun"
    information_via = "steward"

    def __init__(self, agents: dict[str, AppStaffAgent], reporter: RunReporter, task_plans: dict[str, TaskPlan]) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.last_plan: TaskPlan | None = None

    def run(self, task: str, run_dir: Path) -> bool:
        self.reporter.event("runtime_start", runtime=self.name, task=task, execution_backend="vendored_mobilerun")
        plan = StewardAgent(self.agents, self.reporter, task_plans=self.task_plans, mode=self.name).plan(task)
        self.last_plan = plan
        executor = MobileRunExecutor(self.reporter)
        completed_results: dict[str, str] = {}
        for subtask in plan.subtasks:
            subtask = self._with_delivered_information(plan, subtask, completed_results)
            agent_name = f"{subtask.agent_name}_agent"
            self.reporter.state_event(agent_name, "RUNNING", runtime=self.name)
            result = executor.run_subtask(
                agent_name=agent_name,
                subtask=subtask,
                run_dir=run_dir,
                runtime=self.name,
                completion_probe=self._completion_probe(plan, subtask),
            )
            self.reporter.state_event(agent_name, "DONE" if result.success else "FAILED", runtime=self.name, reason=result.reason)
            if not result.success:
                self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason=result.reason)
                return False
            completed_results[subtask.agent_name] = result.reason
        self.reporter.event("runtime_finish", runtime=self.name, task=task, success=True)
        return True

    def _with_delivered_information(self, plan: TaskPlan, subtask, completed_results: dict[str, str]):
        incoming = []
        for flow in plan.information_flows:
            if flow.to_agent != subtask.agent_name:
                continue
            information = completed_results.get(flow.from_agent, "").strip()
            if not information:
                continue
            request_id = f"{self.name}_flow_{flow.name}_{flow.from_agent}_{flow.to_agent}"
            self.reporter.ipc_event(
                request_id=request_id,
                message_kind="RuntimeInformationResponse",
                status="delivered",
                from_agent=f"{flow.from_agent}_agent",
                to_agent=f"{flow.to_agent}_agent",
                mode=self.name,
                via=self.information_via,
                response_summary=information,
                evidence=information,
            )
            incoming.append(f"- From {flow.from_agent}_agent via {flow.name}: {information}")
        if not incoming:
            return subtask
        instruction = (
            f"{subtask.instruction}\n\n"
            "Planner-declared information already collected from previous app agents:\n"
            + "\n".join(incoming)
            + "\nUse this information directly when it is relevant."
        )
        return replace(subtask, instruction=instruction)

    def _completion_probe(self, plan: TaskPlan, subtask) -> Callable[[], str | None] | None:
        if subtask.agent_name != "calendar":
            return None
        visible_terms = plan.success_criteria.get("visible_terms", [])
        required_terms = [term.strip().lower() for term in visible_terms if isinstance(term, str) and term.strip()]
        if not required_terms:
            return None
        adb = next(iter(self.agents.values())).adb

        def probe() -> str | None:
            proc = adb.shell(
                "content",
                "query",
                "--uri",
                "content://com.android.calendar/events",
                "--projection",
                "title:eventLocation:description:dtstart",
                timeout=20,
            )
            rows = [line for line in proc.stdout.splitlines() if line.startswith("Row:")]
            matching_rows = [row for row in rows if all(term in row.lower() for term in required_terms)]
            if matching_rows:
                return "; ".join(matching_rows[:3])
            return None

        return probe


class MobileRunStewardSerialRuntime(_MobileRunRuntimeBase):
    name = "mobilerun_steward_serial"
    information_via = "steward"


class MobileRunAgentOSRuntime(_MobileRunRuntimeBase):
    name = "mobilerun_agentos_parallel"
    information_via = "peer"

    def run(self, task: str, run_dir: Path) -> bool:
        self.reporter.event("runtime_start", runtime=self.name, task=task, execution_backend="vendored_mobilerun_parallel")
        plan = StewardAgent(self.agents, self.reporter, task_plans=self.task_plans, mode=self.name).plan(task)
        self.last_plan = plan
        flows = tuple(plan.information_flows)
        if not flows:
            return super().run(task, run_dir)

        primary_flow = flows[0]
        requester, provider = self._requester_provider(primary_flow.from_agent, primary_flow.to_agent)
        requester_subtask = next((item for item in plan.subtasks if item.agent_name == requester), None)
        provider_subtask = next((item for item in plan.subtasks if item.agent_name == provider), None)
        if requester_subtask is None or provider_subtask is None:
            return super().run(task, run_dir)

        executor = MobileRunExecutor(self.reporter)
        display_map = self._display_assignment((provider, requester))
        provider_agent_name = f"{provider}_agent"
        requester_agent_name = f"{requester}_agent"

        prep_subtask = replace(
            requester_subtask,
            instruction=(
                f"{requester_subtask.instruction}\n\n"
                "AgentOS preparation phase: open the relevant app screen and fill only information that is already present in this subtask or the user's original goal. "
                "If another app agent must provide information or complete an operation, stay in your own app, expose or request that dependency if the UI supports it, then stop before saving, sending, confirming, or creating the final record. Do not open or operate the peer app yourself."
            ),
            max_steps=min(max(3, requester_subtask.max_steps), 5),
        )

        self.reporter.ipc_event(
            request_id=f"planner_flow_{primary_flow.name}_{provider}_{requester}",
            message_kind="RuntimeInformationRequest",
            status="created",
            from_agent=requester_agent_name,
            to_agent=provider_agent_name,
            mode=self.name,
            via="peer",
            request_summary=f"{primary_flow.name}: {', '.join(primary_flow.fields) or 'planner-declared information'}",
            policy_decision="not_checked",
        )

        initial_results = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            submitted = {
                pool.submit(
                    self._run_display_subtask,
                    executor,
                    provider_agent_name,
                    provider_subtask,
                    run_dir,
                    display_map.get(provider_agent_name, {}),
                    "provider_full",
                ): provider_agent_name,
                pool.submit(
                    self._run_display_subtask,
                    executor,
                    requester_agent_name,
                    prep_subtask,
                    run_dir,
                    display_map.get(requester_agent_name, {}),
                    "requester_prepare",
                ): requester_agent_name,
            }
            for future in as_completed(submitted):
                agent_name = submitted[future]
                initial_results[agent_name] = future.result()

        provider_result = initial_results[provider_agent_name]
        prep_result = initial_results[requester_agent_name]
        if not provider_result.success:
            self.reporter.event("runtime_finish", runtime=self.name, task=task, success=False, reason=f"{provider_agent_name} failed: {provider_result.reason}")
            return False
        if not prep_result.success:
            self.reporter.event("agent_prepare_incomplete", runtime=self.name, agent=requester_agent_name, reason=prep_result.reason)

        self.reporter.ipc_event(
            request_id=f"planner_flow_{primary_flow.name}_{provider}_{requester}",
            message_kind="RuntimeInformationResponse",
            status="delivered",
            from_agent=provider_agent_name,
            to_agent=requester_agent_name,
            mode=self.name,
            via="peer",
            request_summary=f"{primary_flow.name}: {', '.join(primary_flow.fields) or 'planner-declared information'}",
            response_summary=provider_result.reason,
            evidence=provider_result.reason,
            policy_decision="not_checked",
        )
        resume_subtask = replace(
            requester_subtask,
            instruction=(
                f"{requester_subtask.instruction}\n\n"
                "AgentOS resume phase: peer information has arrived from "
                f"{provider_agent_name}:\n{provider_result.reason}\n\n"
                "Use this information directly where relevant and complete the final app task."
            ),
            max_steps=max(requester_subtask.max_steps, 10),
        )
        self.reporter.state_event(requester_agent_name, "READY", runtime=self.name, phase="resume_after_peer")
        final_result = self._run_display_subtask(
            executor,
            requester_agent_name,
            resume_subtask,
            run_dir,
            display_map.get(requester_agent_name, {}),
            "requester_resume",
            completion_probe=self._completion_probe(plan, resume_subtask),
        )
        success = final_result.success
        self.reporter.event("runtime_finish", runtime=self.name, task=task, success=success, reason=final_result.reason)
        return success

    def _requester_provider(self, source: str, target: str) -> tuple[str, str]:
        target_agent = self.agents.get(target)
        target_caps = set(getattr(getattr(target_agent, "config", None), "capabilities", ()))
        if target_caps & {"authorize_payment", "decline_payment"}:
            return source, target
        return target, source

    def _run_display_subtask(
        self,
        executor: MobileRunExecutor,
        agent_name: str,
        subtask,
        run_dir: Path,
        display: dict[str, object],
        phase: str,
        completion_probe: Callable[[], str | None] | None = None,
    ):
        self.reporter.state_event(agent_name, "RUNNING", runtime=self.name, phase=phase, display_id=display.get("display_id"))
        result = executor.run_subtask(
            agent_name=agent_name,
            subtask=subtask,
            run_dir=run_dir,
            runtime=self.name,
            completion_probe=completion_probe,
            display_id=display.get("display_id") if isinstance(display.get("display_id"), int) else None,
            surfaceflinger_id=str(display.get("surfaceflinger_id") or "") or None,
        )
        self.reporter.state_event(agent_name, "DONE" if result.success else "FAILED", runtime=self.name, phase=phase, display_id=display.get("display_id"), reason=result.reason)
        return result

    def _display_assignment(self, agent_keys: tuple[str, str]) -> dict[str, dict[str, object]]:
        adb = next(iter(self.agents.values())).adb
        displays = adb.list_displays()
        virtuals = [display for display in displays if display.display_id != 0 and display.surfaceflinger_id]
        source, target = agent_keys
        assignment: dict[str, dict[str, object]] = {
            f"{target}_agent": {"display_id": 0, "surfaceflinger_id": None},
        }
        if virtuals:
            assignment[f"{source}_agent"] = {
                "display_id": virtuals[0].display_id,
                "surfaceflinger_id": virtuals[0].surfaceflinger_id,
            }
        else:
            assignment[f"{source}_agent"] = {"display_id": None, "surfaceflinger_id": None}
        self.reporter.event("display_assignment", runtime=self.name, assignment=assignment)
        return assignment
