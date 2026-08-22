from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    SOURCE = "SOURCE"
    WORK = "WORK"
    SINK = "SINK"


class NodeStatus(StrEnum):
    BLOCKED = "BLOCKED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ArtifactDraft:
    kind: str
    payload: dict[str, Any]
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    producer_node_id: str
    kind: str
    payload: dict[str, Any]
    evidence_refs: tuple[str, ...]
    graph_version: int


@dataclass(frozen=True)
class WorkSpec:
    node_id: str
    agent_id: str
    goal: str
    expected_artifact_kinds: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: NodeKind
    agent_id: str | None
    goal: str
    predecessors: tuple[str, ...] = ()
    expected_artifact_kinds: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.BLOCKED
    assignment_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    outcome: str | None = None
    created_order: int = 0


@dataclass(frozen=True)
class Edge:
    from_node_id: str
    to_node_id: str
    artifact_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphEvent:
    graph_id: str
    version: int
    kind: str
    node_ids: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphSnapshot:
    graph_id: str
    version: int
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    artifacts: tuple[Artifact, ...]

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def artifact(self, artifact_id: str) -> Artifact:
        for artifact in self.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        raise KeyError(artifact_id)

    def input_artifacts(self, node_id: str) -> tuple[Artifact, ...]:
        predecessor_ids = {edge.from_node_id for edge in self.edges if edge.to_node_id == node_id}
        return tuple(artifact for artifact in self.artifacts if artifact.producer_node_id in predecessor_ids)

    def ready_work_nodes(self) -> tuple[Node, ...]:
        """Nodes with zero remaining predecessor dependencies."""
        return tuple(node for node in self.nodes if node.kind is NodeKind.WORK and node.status is NodeStatus.READY)
