from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..graph_space.models import GraphSnapshot, Node, NodeKind


@dataclass(frozen=True)
class SchedulingCandidate:
    graph_id: str
    graph_order: int
    snapshot: GraphSnapshot
    node: Node


class SchedulingPolicy(Protocol):
    name: str

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        ...


class FifoPolicy:
    name = "fifo"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        return tuple(sorted(candidates, key=lambda item: (item.graph_order, item.node.created_order)))


class CriticalPathPolicy:
    name = "critical_path"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        ranks = _candidate_metrics(candidates, upward_ranks)
        return tuple(sorted(candidates, key=lambda item: (-ranks[(item.graph_id, item.node.node_id)], item.graph_order, item.node.created_order)))


class FanoutPolicy:
    name = "fanout"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        descendants = _candidate_metrics(candidates, descendant_counts)
        return tuple(sorted(candidates, key=lambda item: (-descendants[(item.graph_id, item.node.node_id)], item.graph_order, item.node.created_order)))


@dataclass(frozen=True)
class HybridPolicy:
    critical_path_weight: float = 1.0
    fanout_weight: float = 1.0
    duration_weight: float = 0.0
    waiting_age_weight: float = 0.0
    name: str = "hybrid"

    def order(self, candidates: tuple[SchedulingCandidate, ...]) -> tuple[SchedulingCandidate, ...]:
        ranks = _candidate_metrics(candidates, upward_ranks)
        descendants = _candidate_metrics(candidates, descendant_counts)

        def score(item: SchedulingCandidate) -> float:
            node = item.node
            waiting_age = float(node.metadata.get("waiting_age", 0.0))
            return (
                self.critical_path_weight * ranks[(item.graph_id, node.node_id)]
                + self.fanout_weight * descendants[(item.graph_id, node.node_id)]
                - self.duration_weight * estimated_duration(node)
                + self.waiting_age_weight * waiting_age
            )

        return tuple(sorted(candidates, key=lambda item: (-score(item), item.graph_order, item.node.created_order)))


def _candidate_metrics(
    candidates: tuple[SchedulingCandidate, ...],
    metric: Callable[[GraphSnapshot], dict[str, float] | dict[str, int]],
) -> dict[tuple[str, str], float]:
    by_graph: dict[str, GraphSnapshot] = {item.graph_id: item.snapshot for item in candidates}
    values: dict[tuple[str, str], float] = {}
    for graph_id, snapshot in by_graph.items():
        graph_values = metric(snapshot)
        values.update({(graph_id, node_id): float(value) for node_id, value in graph_values.items()})
    return values


def estimated_duration(node: Node) -> float:
    value = node.metadata.get("estimated_duration", 1.0)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 1.0


def upward_ranks(snapshot: GraphSnapshot) -> dict[str, float]:
    successors: dict[str, list[str]] = {node.node_id: [] for node in snapshot.nodes}
    nodes = {node.node_id: node for node in snapshot.nodes}
    for edge in snapshot.edges:
        successors[edge.from_node_id].append(edge.to_node_id)
    cache: dict[str, float] = {}

    def rank(node_id: str) -> float:
        if node_id in cache:
            return cache[node_id]
        node = nodes[node_id]
        own = estimated_duration(node) if node.kind is NodeKind.WORK else 0.0
        children = successors[node_id]
        cache[node_id] = own + (max(rank(child) for child in children) if children else 0.0)
        return cache[node_id]

    for node_id in nodes:
        rank(node_id)
    return cache


def descendant_counts(snapshot: GraphSnapshot) -> dict[str, int]:
    successors: dict[str, list[str]] = {node.node_id: [] for node in snapshot.nodes}
    nodes = {node.node_id: node for node in snapshot.nodes}
    for edge in snapshot.edges:
        successors[edge.from_node_id].append(edge.to_node_id)
    cache: dict[str, frozenset[str]] = {}

    def descendants(node_id: str) -> frozenset[str]:
        if node_id in cache:
            return cache[node_id]
        found: set[str] = set()
        for child in successors[node_id]:
            if nodes[child].kind is NodeKind.WORK:
                found.add(child)
            found.update(descendants(child))
        cache[node_id] = frozenset(found)
        return cache[node_id]

    return {node_id: len(descendants(node_id)) for node_id in nodes}
