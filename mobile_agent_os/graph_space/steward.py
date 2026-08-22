from __future__ import annotations

import copy
import json
from dataclasses import asdict
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Callable

from .models import Artifact, ArtifactDraft, Edge, GraphEvent, GraphSnapshot, Node, NodeKind, NodeStatus, WorkSpec
from .registry import RegistryTable


class GraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class InitialGraph:
    graph_id: str
    source_id: str
    sink_id: str
    work: tuple[WorkSpec, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True)
class CheckpointExpansion:
    origin_node_id: str
    assignment_id: str
    checkpoint: ArtifactDraft
    provider: WorkSpec
    continuation: WorkSpec
    request_kind: str
    required_capability: str


@dataclass
class _GraphRecord:
    version: int
    nodes: dict[str, Node]
    edges: list[Edge]
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    events: list[GraphEvent] = field(default_factory=list)
    next_artifact: int = 1


class GraphSteward:
    """The sole mutable owner of graph topology, node state, and artifacts."""

    def __init__(self, registry: RegistryTable) -> None:
        self.registry = registry
        self._graphs: dict[str, _GraphRecord] = {}
        self._subscribers: list[Callable[[GraphEvent], None]] = []
        self._lock = RLock()

    def subscribe(self, callback: Callable[[GraphEvent], None]) -> None:
        self._subscribers.append(callback)

    def create_initial_graph(self, spec: InitialGraph) -> GraphSnapshot:
        with self._lock:
            if spec.graph_id in self._graphs:
                raise GraphError(f"graph already exists: {spec.graph_id}")
            node_ids = {spec.source_id, spec.sink_id}
            if len(node_ids) != 2:
                raise GraphError("source and sink ids must differ")
            nodes = {
                spec.source_id: Node(spec.source_id, NodeKind.SOURCE, None, "Task start", status=NodeStatus.DONE, outcome="source", created_order=0),
                spec.sink_id: Node(spec.sink_id, NodeKind.SINK, None, "Task evaluation", created_order=len(spec.work) + 1),
            }
            for index, work in enumerate(spec.work, start=1):
                if work.node_id in node_ids:
                    raise GraphError(f"duplicate node id: {work.node_id}")
                if work.agent_id not in {profile.app_id for profile in self.registry.profiles()}:
                    raise GraphError(f"unknown AppAgent: {work.agent_id}")
                node_ids.add(work.node_id)
                nodes[work.node_id] = Node(
                    node_id=work.node_id,
                    kind=NodeKind.WORK,
                    agent_id=work.agent_id,
                    goal=work.goal,
                    expected_artifact_kinds=work.expected_artifact_kinds,
                    required_resources=work.required_resources,
                    metadata=copy.deepcopy(work.metadata),
                    created_order=index,
                )
            self._validate_edges(nodes, spec.edges)
            record = _GraphRecord(version=0, nodes=nodes, edges=list(spec.edges))
            self._graphs[spec.graph_id] = record
            self._refresh_readiness(record)
            event = self._record_event(record, spec.graph_id, "graph_created", tuple(nodes))
        self._notify(event)
        return self.read(spec.graph_id)

    def read(self, graph_id: str, *, include_artifacts: bool = True) -> GraphSnapshot:
        with self._lock:
            record = self._record(graph_id)
            return GraphSnapshot(
                graph_id=graph_id,
                version=record.version,
                nodes=tuple(copy.deepcopy(record.nodes[key]) for key in sorted(record.nodes, key=lambda item: record.nodes[item].created_order)),
                edges=tuple(copy.deepcopy(record.edges)),
                artifacts=tuple(copy.deepcopy(record.artifacts[key]) for key in sorted(record.artifacts)) if include_artifacts else (),
            )

    def read_for_scheduler(self, graph_id: str) -> GraphSnapshot:
        """Scheduling receives topology and state, never artifact payloads."""
        return self.read(graph_id, include_artifacts=False)

    def read_for_node(self, graph_id: str, node_id: str) -> GraphSnapshot:
        """An AppAgent receives public graph structure plus only its predecessor artifacts."""
        with self._lock:
            record = self._record(graph_id)
            if node_id not in record.nodes:
                raise GraphError(f"unknown node: {node_id}")
            predecessor_ids = {edge.from_node_id for edge in record.edges if edge.to_node_id == node_id}
            snapshot = self.read(graph_id, include_artifacts=False)
            artifacts = tuple(
                copy.deepcopy(artifact)
                for artifact in record.artifacts.values()
                if artifact.producer_node_id in predecessor_ids
            )
            return replace(snapshot, artifacts=artifacts)

    def events(self, graph_id: str) -> tuple[GraphEvent, ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._record(graph_id).events))

    def write_events_jsonl(self, graph_id: str, path: str) -> None:
        """Export the append-only graph event log for an external trace consumer."""
        events = self.events(graph_id)
        with open(path, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def assign(self, graph_id: str, node_id: str, assignment_id: str) -> None:
        with self._lock:
            record = self._record(graph_id)
            node = record.nodes[node_id]
            if node.kind is not NodeKind.WORK or node.status is not NodeStatus.READY:
                raise GraphError(f"node is not assignable: {node_id} ({node.status})")
            record.nodes[node_id] = replace(node, status=NodeStatus.ASSIGNED, assignment_id=assignment_id)
            event = self._record_event(record, graph_id, "assignment_created", (node_id,), {"assignment_id": assignment_id})
        self._notify(event)

    def start(self, graph_id: str, node_id: str, assignment_id: str) -> None:
        with self._lock:
            record = self._record(graph_id)
            node = record.nodes[node_id]
            self._require_assignment(node, assignment_id, NodeStatus.ASSIGNED)
            record.nodes[node_id] = replace(node, status=NodeStatus.RUNNING)
            event = self._record_event(record, graph_id, "assignment_acknowledged", (node_id,), {"assignment_id": assignment_id})
        self._notify(event)

    def commit_node(self, graph_id: str, node_id: str, assignment_id: str, artifacts: tuple[ArtifactDraft, ...] = ()) -> GraphSnapshot:
        with self._lock:
            record = self._record(graph_id)
            node = record.nodes[node_id]
            self._require_assignment(node, assignment_id, NodeStatus.RUNNING)
            rejection = self._completion_rejection(node, artifacts)
            if rejection:
                record.nodes[node_id] = replace(node, status=NodeStatus.FAILED, outcome=rejection)
                self._refresh_readiness(record)
                event = self._record_event(
                    record,
                    graph_id,
                    "completion_rejected",
                    (node_id,),
                    {"assignment_id": assignment_id, "reason": rejection},
                )
                # A rejected report never becomes a graph artifact.
                artifact_ids: tuple[str, ...] = ()
            else:
                artifact_ids = self._publish_artifacts(record, node_id, artifacts)
                record.nodes[node_id] = replace(node, status=NodeStatus.DONE, artifact_ids=artifact_ids, outcome="completed")
                self._refresh_readiness(record)
                event = self._record_event(record, graph_id, "node_completed", (node_id,), {"assignment_id": assignment_id, "artifact_ids": artifact_ids})
        self._notify(event)
        return self.read(graph_id)

    def fail_node(self, graph_id: str, node_id: str, assignment_id: str, reason: str) -> GraphSnapshot:
        with self._lock:
            record = self._record(graph_id)
            node = record.nodes[node_id]
            self._require_assignment(node, assignment_id, NodeStatus.RUNNING)
            record.nodes[node_id] = replace(node, status=NodeStatus.FAILED, outcome=reason)
            self._refresh_readiness(record)
            event = self._record_event(record, graph_id, "node_failed", (node_id,), {"assignment_id": assignment_id, "reason": reason})
        self._notify(event)
        return self.read(graph_id)

    def checkpoint_and_expand(self, graph_id: str, request: CheckpointExpansion) -> GraphSnapshot:
        with self._lock:
            record = self._record(graph_id)
            origin = record.nodes[request.origin_node_id]
            self._require_assignment(origin, request.assignment_id, NodeStatus.RUNNING)
            if request.provider.node_id in record.nodes or request.continuation.node_id in record.nodes:
                raise GraphError("expansion node id already exists")
            if not self.registry.providers(request.required_capability):
                raise GraphError(f"no registry provider for capability: {request.required_capability}")
            if request.provider.agent_id not in {profile.app_id for profile in self.registry.profiles()}:
                raise GraphError(f"unknown provider AppAgent: {request.provider.agent_id}")
            if request.required_capability not in self.registry.get(request.provider.agent_id).capabilities:
                raise GraphError("selected provider does not own requested capability")
            if request.continuation.agent_id != origin.agent_id:
                raise GraphError("continuation must remain owned by the origin AppAgent")

            successors = [edge.to_node_id for edge in record.edges if edge.from_node_id == origin.node_id]
            checkpoint_ids = self._publish_artifacts(record, origin.node_id, (request.checkpoint,))
            record.nodes[origin.node_id] = replace(origin, status=NodeStatus.DONE, artifact_ids=checkpoint_ids, outcome="checkpoint")
            next_order = max(node.created_order for node in record.nodes.values()) + 1
            record.nodes[request.provider.node_id] = self._work_node(request.provider, next_order)
            record.nodes[request.continuation.node_id] = self._work_node(request.continuation, next_order + 1)
            record.edges = [edge for edge in record.edges if edge.from_node_id != origin.node_id]
            record.edges.extend(
                [
                    Edge(origin.node_id, request.continuation.node_id, (request.checkpoint.kind,)),
                    Edge(request.provider.node_id, request.continuation.node_id, request.provider.expected_artifact_kinds),
                    *(Edge(request.continuation.node_id, successor) for successor in successors),
                ]
            )
            self._refresh_readiness(record)
            event = self._record_event(
                record,
                graph_id,
                "graph_expanded",
                (origin.node_id, request.provider.node_id, request.continuation.node_id, *successors),
                {"request_kind": request.request_kind, "required_capability": request.required_capability},
            )
        self._notify(event)
        return self.read(graph_id)

    def evaluate_sink(self, graph_id: str, *, success: bool, evidence_refs: tuple[str, ...] = ()) -> GraphSnapshot:
        with self._lock:
            record = self._record(graph_id)
            sinks = [node for node in record.nodes.values() if node.kind is NodeKind.SINK]
            if len(sinks) != 1 or sinks[0].status is not NodeStatus.READY:
                raise GraphError("sink is not ready for evaluation")
            sink = sinks[0]
            record.nodes[sink.node_id] = replace(sink, status=NodeStatus.DONE if success else NodeStatus.FAILED, outcome="evaluation", metadata={"evidence_refs": evidence_refs})
            event = self._record_event(record, graph_id, "sink_evaluated", (sink.node_id,), {"success": success})
        self._notify(event)
        return self.read(graph_id)

    def _work_node(self, spec: WorkSpec, created_order: int) -> Node:
        return Node(spec.node_id, NodeKind.WORK, spec.agent_id, spec.goal, expected_artifact_kinds=spec.expected_artifact_kinds, required_resources=spec.required_resources, metadata=copy.deepcopy(spec.metadata), created_order=created_order)

    def _publish_artifacts(self, record: _GraphRecord, node_id: str, drafts: tuple[ArtifactDraft, ...]) -> tuple[str, ...]:
        artifact_ids = []
        for draft in drafts:
            artifact_id = f"A{record.next_artifact}"
            record.next_artifact += 1
            record.artifacts[artifact_id] = Artifact(artifact_id, node_id, draft.kind, copy.deepcopy(draft.payload), tuple(draft.evidence_refs), record.version + 1)
            artifact_ids.append(artifact_id)
        return tuple(artifact_ids)

    @staticmethod
    def _completion_rejection(node: Node, drafts: tuple[ArtifactDraft, ...]) -> str | None:
        """Validate the generic graph delivery contract before publishing a result."""
        expected = set(node.expected_artifact_kinds)
        if not expected and not drafts:
            return None
        produced = {draft.kind for draft in drafts}
        missing = expected - produced
        if missing:
            return f"completion missing required artifact kinds: {', '.join(sorted(missing))}"
        if not drafts:
            return "completion produced no artifact"
        for draft in drafts:
            value = draft.payload.get("value")
            evidence = draft.payload.get("evidence")
            if not GraphSteward._has_content(value):
                return f"completion artifact {draft.kind} has no result value"
            if not GraphSteward._has_content(evidence):
                return f"completion artifact {draft.kind} has no supporting evidence"
        return None

    @staticmethod
    def _has_content(value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (tuple, list, dict, set)):
            return bool(value)
        return True

    def _refresh_readiness(self, record: _GraphRecord) -> None:
        predecessors: dict[str, list[str]] = {node_id: [] for node_id in record.nodes}
        for edge in record.edges:
            predecessors[edge.to_node_id].append(edge.from_node_id)
        for node_id, node in list(record.nodes.items()):
            if node.kind is NodeKind.SOURCE or node.status not in {NodeStatus.BLOCKED, NodeStatus.READY}:
                continue
            prior = predecessors[node_id]
            failed = tuple(item for item in prior if record.nodes[item].status is NodeStatus.FAILED)
            metadata = dict(node.metadata)
            if failed:
                metadata["blocked_by_failed_predecessors"] = failed
                record.nodes[node_id] = replace(node, predecessors=tuple(prior), metadata=metadata, status=NodeStatus.BLOCKED)
            elif prior and all(record.nodes[item].status is NodeStatus.DONE for item in prior):
                metadata.pop("blocked_by_failed_predecessors", None)
                record.nodes[node_id] = replace(node, predecessors=tuple(prior), metadata=metadata, status=NodeStatus.READY)
            elif not prior and node.kind is NodeKind.WORK:
                metadata.pop("blocked_by_failed_predecessors", None)
                record.nodes[node_id] = replace(node, predecessors=(), metadata=metadata, status=NodeStatus.READY)
            else:
                metadata.pop("blocked_by_failed_predecessors", None)
                record.nodes[node_id] = replace(node, predecessors=tuple(prior), metadata=metadata, status=NodeStatus.BLOCKED)

    def _record_event(self, record: _GraphRecord, graph_id: str, kind: str, node_ids: tuple[str, ...], detail: dict[str, object] | None = None) -> GraphEvent:
        record.version += 1
        event = GraphEvent(graph_id, record.version, kind, node_ids, dict(detail or {}))
        record.events.append(event)
        return event

    def _validate_edges(self, nodes: dict[str, Node], edges: tuple[Edge, ...]) -> None:
        for edge in edges:
            if edge.from_node_id not in nodes or edge.to_node_id not in nodes:
                raise GraphError(f"edge refers to unknown node: {edge}")
            if edge.from_node_id == edge.to_node_id:
                raise GraphError("self edge is not allowed")
        successors: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in edges:
            successors[edge.from_node_id].append(edge.to_node_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise GraphError("execution graph must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for successor in successors[node_id]:
                visit(successor)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in nodes:
            visit(node_id)

    def _record(self, graph_id: str) -> _GraphRecord:
        try:
            return self._graphs[graph_id]
        except KeyError as exc:
            raise GraphError(f"unknown graph: {graph_id}") from exc

    @staticmethod
    def _require_assignment(node: Node, assignment_id: str, expected: NodeStatus) -> None:
        if node.status is not expected or node.assignment_id != assignment_id:
            raise GraphError(f"invalid assignment transition for {node.node_id}")

    def _notify(self, event: GraphEvent) -> None:
        for callback in tuple(self._subscribers):
            callback(event)
