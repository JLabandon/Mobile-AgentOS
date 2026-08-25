from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WorkStatus(StrEnum):
    BLOCKED = "BLOCKED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ArtifactState(StrEnum):
    FUTURE = "FUTURE"
    CONCRETE = "CONCRETE"
    INVALIDATED = "INVALIDATED"
    FAILED = "FAILED"


class ReusePolicy(StrEnum):
    INDEXED = "INDEXED"
    UNINDEXED = "UNINDEXED"


class TaskStatus(StrEnum):
    ACTIVE = "ACTIVE"
    READY_FOR_EVALUATION = "READY_FOR_EVALUATION"
    DONE = "DONE"
    FAILED = "FAILED"


class EdgeKind(StrEnum):
    PRECEDES = "PRECEDES"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"


class ResolutionKind(StrEnum):
    REUSED_CONCRETE = "REUSED_CONCRETE"
    JOINED_FUTURE = "JOINED_FUTURE"
    CREATED_FUTURE = "CREATED_FUTURE"
    CREATED_UNINDEXED = "CREATED_UNINDEXED"


@dataclass(frozen=True)
class ArtifactIdentityCandidate:
    schema_id: str
    parameters: dict[str, Any]
    security_scope: str = ""


@dataclass(frozen=True)
class ArtifactKey:
    schema_id: str
    schema_version: int
    canonical_parameters: tuple[tuple[str, str], ...]
    security_scope: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def parameters(self) -> dict[str, Any]:
        return {name: json.loads(value) for name, value in self.canonical_parameters}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "canonical_parameters": self.parameters(),
            "security_scope": self.security_scope,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactKey":
        parameters = value.get("canonical_parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("canonical_parameters must be an object")
        canonical = tuple(
            (str(name), json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            for name, item in sorted(parameters.items())
        )
        return cls(
            schema_id=str(value["schema_id"]),
            schema_version=int(value["schema_version"]),
            canonical_parameters=canonical,
            security_scope=str(value["security_scope"]),
        )


@dataclass(frozen=True)
class ArtifactDraft:
    kind: str
    payload: dict[str, Any]
    evidence_refs: tuple[str, ...] = ()
    artifact_node_id: str | None = None
    observed_at: float | None = None
    valid_until: float | None = None
    source_revision: str = ""


@dataclass(frozen=True)
class WorkSpec:
    node_id: str
    agent_id: str
    goal: str
    required_resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlEdgeSpec:
    from_work_id: str
    to_work_id: str


@dataclass(frozen=True)
class ArtifactSpec:
    local_id: str
    kind: str
    producer_work_id: str
    consumer_work_ids: tuple[str, ...]
    identity: ArtifactIdentityCandidate | None = None
    freshness_requirement_seconds: float | None = None


@dataclass(frozen=True)
class GraphFragment:
    task_id: str
    user_goal: str
    work: tuple[WorkSpec, ...]
    control_edges: tuple[ControlEdgeSpec, ...] = ()
    artifacts: tuple[ArtifactSpec, ...] = ()
    terminal_work_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactRequirement:
    artifact_node_id: str
    max_age_seconds: float | None = None


@dataclass(frozen=True)
class WorkNode:
    node_id: str
    agent_id: str
    goal: str
    control_predecessors: tuple[str, ...] = ()
    input_artifacts: tuple[ArtifactRequirement, ...] = ()
    output_artifact_ids: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    task_memberships: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    status: WorkStatus = WorkStatus.BLOCKED
    assignment_id: str | None = None
    outcome: str | None = None
    completion_report: tuple[dict[str, Any], ...] = ()
    created_order: int = 0


@dataclass(frozen=True)
class ArtifactNode:
    node_id: str
    kind: str
    state: ArtifactState
    producer_work_id: str
    consumer_work_ids: tuple[str, ...] = ()
    key: ArtifactKey | None = None
    reuse_policy: ReusePolicy = ReusePolicy.UNINDEXED
    generation: int = 1
    payload: dict[str, Any] | None = None
    evidence_refs: tuple[str, ...] = ()
    task_memberships: tuple[str, ...] = ()
    observed_at: float | None = None
    valid_from: float | None = None
    valid_until: float | None = None
    source_revision: str = ""
    failure_reason: str | None = None
    created_order: int = 0


@dataclass(frozen=True)
class DependencyEdge:
    from_node_id: str
    to_node_id: str
    kind: EdgeKind


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    user_goal: str
    entry_work_ids: tuple[str, ...]
    terminal_work_ids: tuple[str, ...]
    referenced_work_ids: tuple[str, ...]
    referenced_artifact_ids: tuple[str, ...]
    local_node_map: tuple[tuple[str, str], ...]
    submitted_at: float
    status: TaskStatus = TaskStatus.ACTIVE
    outcome: str | None = None

    def global_id(self, local_id: str) -> str:
        for candidate, global_id in self.local_node_map:
            if candidate == local_id:
                return global_id
        raise KeyError(local_id)


@dataclass(frozen=True)
class GraphEvent:
    version: int
    kind: str
    task_ids: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GlobalGraphSnapshot:
    version: int
    work_nodes: tuple[WorkNode, ...]
    artifact_nodes: tuple[ArtifactNode, ...]
    edges: tuple[DependencyEdge, ...]
    tasks: tuple[TaskRecord, ...]

    def work(self, node_id: str) -> WorkNode:
        for node in self.work_nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def artifact(self, node_id: str) -> ArtifactNode:
        for node in self.artifact_nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def task(self, task_id: str) -> TaskRecord:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def input_artifact_nodes(self, work_id: str) -> tuple[ArtifactNode, ...]:
        required = {item.artifact_node_id for item in self.work(work_id).input_artifacts}
        return tuple(node for node in self.artifact_nodes if node.node_id in required)

    def ready_work_nodes(self) -> tuple[WorkNode, ...]:
        return tuple(node for node in self.work_nodes if node.status is WorkStatus.READY)

    def work_successors(self) -> dict[str, tuple[str, ...]]:
        successors: dict[str, set[str]] = {node.node_id: set() for node in self.work_nodes}
        for edge in self.edges:
            if edge.kind is EdgeKind.PRECEDES:
                successors[edge.from_node_id].add(edge.to_node_id)
            elif edge.kind is EdgeKind.PRODUCES:
                artifact = self.artifact(edge.to_node_id)
                successors[edge.from_node_id].update(artifact.consumer_work_ids)
        return {node_id: tuple(sorted(values)) for node_id, values in successors.items()}


@dataclass(frozen=True)
class ArtifactResolution:
    kind: ResolutionKind
    artifact_node_id: str
    producer_work_id: str
