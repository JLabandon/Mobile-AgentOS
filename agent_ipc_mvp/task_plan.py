from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    goal: str
    subtasks: tuple[Any, ...]
    edges: tuple[tuple[str, str], ...]
    mode: str
