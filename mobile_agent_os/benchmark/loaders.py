from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..agents import AppConfig, SubTask
from ..task_plan import InformationFlow, TaskPlan


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


def _apply_variables(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, list):
        return [_apply_variables(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _apply_variables(item, variables) for key, item in value.items()}
    return value


def load_task_plans(path: Path, runtime: str, variables: dict[str, str] | None = None) -> dict[str, TaskPlan]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if variables:
        raw = _apply_variables(raw, variables)
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
        edges = tuple(tuple(edge) for edge in value.get("edges", []))
        raw_flows = value.get("information_flows", [])
        information_flows = tuple(_parse_information_flow(item) for item in raw_flows) if isinstance(raw_flows, list) else ()
        if not information_flows:
            information_flows = tuple(InformationFlow(from_agent=str(source), to_agent=str(target)) for source, target in edges)
        plans[task_id] = TaskPlan(
            task_id=task_id,
            goal=str(value["goal"]),
            mode=runtime,
            subtasks=subtasks,
            edges=edges,
            information_flows=information_flows,
            success_criteria=dict(value.get("success_criteria", {})),
            environment=dict(value.get("environment", {})),
        )
    return plans


def _parse_information_flow(item: Any) -> InformationFlow:
    if isinstance(item, (list, tuple)) and len(item) == 2:
        return InformationFlow(from_agent=str(item[0]), to_agent=str(item[1]))
    if not isinstance(item, dict):
        raise ValueError(f"bad information flow: {item}")
    contract = item.get("content_contract", {})
    fields = contract.get("fields", []) if isinstance(contract, dict) else item.get("fields", [])
    return InformationFlow(
        from_agent=str(item.get("from_agent", item.get("source", ""))).removesuffix("_agent"),
        to_agent=str(item.get("to_agent", item.get("target", ""))).removesuffix("_agent"),
        name=str(item.get("name", "runtime_information")),
        required=bool(item.get("required", True)),
        delivery=str(item.get("delivery", "on_source_done")),
        fields=tuple(str(field) for field in fields if str(field).strip()) if isinstance(fields, list) else (),
    )
