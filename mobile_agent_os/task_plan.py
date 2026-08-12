from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    goal: str
    mode: str
    subtasks: tuple[Any, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()
    success_criteria: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
