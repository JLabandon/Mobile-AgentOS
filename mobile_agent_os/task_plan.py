from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InformationFlow:
    from_agent: str
    to_agent: str
    name: str = "runtime_information"
    required: bool = True
    delivery: str = "on_source_done"
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    goal: str
    mode: str
    subtasks: tuple[Any, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()
    information_flows: tuple[InformationFlow, ...] = ()
    success_criteria: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
