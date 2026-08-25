from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..graph_space.schema import GlobalGraphSnapshot, WorkNode


@dataclass(frozen=True)
class SchedulingCandidate:
    snapshot: GlobalGraphSnapshot
    work: WorkNode


class SchedulingPolicy(Protocol):
    name: str

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        ...


class FifoPolicy:
    name = "fifo"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        return tuple(sorted(candidates, key=lambda item: item.work.created_order))


class CriticalPathPolicy:
    name = "critical_path"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        ranks = upward_ranks(candidates[0].snapshot) if candidates else {}
        return tuple(sorted(candidates, key=lambda item: (-ranks[item.work.node_id], item.work.created_order)))


class FanoutPolicy:
    name = "fanout"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        descendants = descendant_counts(candidates[0].snapshot) if candidates else {}
        return tuple(sorted(candidates, key=lambda item: (-descendants[item.work.node_id], item.work.created_order)))


@dataclass(frozen=True)
class HybridPolicy:
    critical_path_weight: float = 1.0
    fanout_weight: float = 1.0
    duration_weight: float = 0.0
    waiting_age_weight: float = 0.0
    name: str = "hybrid"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        if not candidates:
            return ()
        ranks = upward_ranks(candidates[0].snapshot)
        descendants = descendant_counts(candidates[0].snapshot)

        def score(item: SchedulingCandidate) -> float:
            work = item.work
            waiting_age = float(work.metadata.get("waiting_age", 0.0))
            return (
                self.critical_path_weight * ranks[work.node_id]
                + self.fanout_weight * descendants[work.node_id]
                - self.duration_weight * estimated_duration(work)
                + self.waiting_age_weight * waiting_age
            )

        return tuple(sorted(candidates, key=lambda item: (-score(item), item.work.created_order)))


def estimated_duration(work: WorkNode) -> float:
    value = work.metadata.get("estimated_duration", 1.0)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 1.0


def upward_ranks(snapshot: GlobalGraphSnapshot) -> dict[str, float]:
    successors = snapshot.work_successors()
    work = {item.node_id: item for item in snapshot.work_nodes}
    cache: dict[str, float] = {}

    def rank(work_id: str) -> float:
        if work_id in cache:
            return cache[work_id]
        children = successors[work_id]
        cache[work_id] = estimated_duration(work[work_id]) + (max(rank(child) for child in children) if children else 0.0)
        return cache[work_id]

    for work_id in work:
        rank(work_id)
    return cache


def descendant_counts(snapshot: GlobalGraphSnapshot) -> dict[str, int]:
    successors = snapshot.work_successors()
    cache: dict[str, frozenset[str]] = {}

    def descendants(work_id: str) -> frozenset[str]:
        if work_id in cache:
            return cache[work_id]
        found: set[str] = set()
        for child in successors[work_id]:
            found.add(child)
            found.update(descendants(child))
        cache[work_id] = frozenset(found)
        return cache[work_id]

    return {work_id: len(descendants(work_id)) for work_id in successors}
