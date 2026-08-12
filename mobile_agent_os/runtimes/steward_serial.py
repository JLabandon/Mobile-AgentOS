from __future__ import annotations

from pathlib import Path

from ..agents import AppStaffAgent
from ..steward import StewardAgent
from ..report import RunReporter
from ..task_plan import TaskPlan


class StewardSerialRuntime:
    name = "steward_serial"

    def __init__(self, agents: dict[str, AppStaffAgent], reporter: RunReporter, task_plans: dict[str, TaskPlan]) -> None:
        self.agents = agents
        self.reporter = reporter
        self.task_plans = task_plans
        self.last_plan: TaskPlan | None = None

    def run(self, task: str, run_dir: Path) -> bool:
        self.reporter.event("runtime_start", runtime=self.name, task=task)
        steward = StewardAgent(self.agents, self.reporter, task_plans=self.task_plans, mode="steward")
        plan = steward.plan(task)
        self.last_plan = plan
        success = steward.run_plan(plan, run_dir)
        self.reporter.event("runtime_finish", runtime=self.name, task=task, success=success)
        return success
