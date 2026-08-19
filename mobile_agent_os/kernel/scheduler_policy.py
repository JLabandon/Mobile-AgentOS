from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ResourceSnapshot:
    name: str
    capacity: int
    leased: int = 0
    persistent_leased: int = 0

    @property
    def available(self) -> int:
        return max(0, self.capacity - self.leased - self.persistent_leased)


@dataclass(frozen=True)
class JobCandidate:
    token: int
    kind: str
    run_id: str = ""
    agent_id: str = ""
    phase: str = ""
    job_type: str = ""
    step: int = 0
    depends_on: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    estimated_duration_s: float = 1.0


@dataclass(frozen=True)
class SchedulerSnapshot:
    candidates: tuple[JobCandidate, ...]
    resources: dict[str, ResourceSnapshot] = field(default_factory=dict)
    completed_runs: frozenset[str] = frozenset()
    running_jobs: int = 0
    max_workers: int = 1


class SchedulerPolicy(Protocol):
    name: str

    def order(self, snapshot: SchedulerSnapshot) -> list[JobCandidate]:
        ...


class FifoSchedulingPolicy:
    name = "fifo"

    def order(self, snapshot: SchedulerSnapshot) -> list[JobCandidate]:
        return sorted(snapshot.candidates, key=lambda item: item.token)


class CriticalPathSchedulingPolicy:
    """Small online policy stub for later learned duration estimates.

    It stays deterministic and conservative: candidates with more unfinished
    successors are ranked first; ties prefer shorter estimated duration and
    then FIFO order.
    """

    name = "critical_path"

    def __init__(self, successor_count: dict[str, int] | None = None) -> None:
        self.successor_count = successor_count or {}

    def order(self, snapshot: SchedulerSnapshot) -> list[JobCandidate]:
        def priority(item: JobCandidate) -> tuple[int, float, int]:
            return (
                -self.successor_count.get(item.run_id, 0),
                item.estimated_duration_s,
                item.token,
            )

        return sorted(snapshot.candidates, key=priority)
