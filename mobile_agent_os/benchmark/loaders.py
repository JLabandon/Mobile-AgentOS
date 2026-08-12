from __future__ import annotations

import json
from pathlib import Path

from ..agents import AppConfig, SubTask
from ..task_plan import TaskPlan


def load_app_configs(path: Path) -> dict[str, AppConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: AppConfig(
            name=name,
            label=value["label"],
            package_candidates=list(value["package_candidates"]),
            launch=dict(value["launch"]),
            capabilities=tuple(value.get("capabilities", [])),
            description=str(value.get("description", "")),
            task_guidelines=tuple(value.get("task_guidelines", [])),
        )
        for name, value in raw.items()
    }


def load_task_plans(path: Path, runtime: str) -> dict[str, TaskPlan]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    plans: dict[str, TaskPlan] = {}
    for task_id, value in raw.items():
        subtasks = tuple(
            SubTask(
                agent_name=str(item["agent_name"]),
                instruction=str(item["instruction"]),
                max_steps=int(item.get("max_steps", 6)),
                required_terms=tuple(item.get("required_terms", [])),
                forbidden_terms=tuple(item.get("forbidden_terms", [])),
                launch_args=tuple(item.get("launch_args", [])),
            )
            for item in value.get("subtasks", [])
        )
        plans[task_id] = TaskPlan(
            task_id=task_id,
            goal=str(value["goal"]),
            mode=runtime,
            subtasks=subtasks,
            edges=tuple(tuple(edge) for edge in value.get("edges", [])),
            success_criteria=dict(value.get("success_criteria", {})),
            environment=dict(value.get("environment", {})),
        )
    return plans
