from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Callable

from .artifact_index import ArtifactIndex, ArtifactIndexError
from .registry import RegistryTable
from .schema import (
    ArtifactDraft,
    ArtifactIdentityCandidate,
    ArtifactKey,
    ArtifactNode,
    ArtifactRequirement,
    ArtifactResolution,
    ArtifactSpec,
    ArtifactState,
    DependencyEdge,
    EdgeKind,
    GlobalGraphSnapshot,
    GraphEvent,
    GraphFragment,
    ResolutionKind,
    ReusePolicy,
    TaskRecord,
    TaskStatus,
    WorkNode,
    WorkSpec,
    WorkStatus,
)


class GraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeExpansion:
    origin_work_id: str
    assignment_id: str
    checkpoint: ArtifactDraft
    provider: WorkSpec
    continuation: WorkSpec
    artifact_kind: str
    identity: ArtifactIdentityCandidate | None
    request_kind: str
    required_capability: str
    freshness_requirement_seconds: float | None = None


@dataclass
class _GlobalGraphRecord:
    version: int = 0
    work: dict[str, WorkNode] = field(default_factory=dict)
    artifacts: dict[str, ArtifactNode] = field(default_factory=dict)
    edges: list[DependencyEdge] = field(default_factory=list)
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    events: list[GraphEvent] = field(default_factory=list)
    next_work: int = 1
    next_artifact: int = 1
    next_created_order: int = 1


class GraphSteward:
    """Sole writer for the global execution graph and its derived ArtifactIndex."""

    def __init__(self, registry: RegistryTable, *, clock: Callable[[], float] = time.time) -> None:
        self.registry = registry
        self._record = _GlobalGraphRecord()
        self._artifact_index = ArtifactIndex()
        self._subscribers: list[Callable[[GraphEvent], None]] = []
        self._clock = clock
        self._lock = RLock()

    def subscribe(self, callback: Callable[[GraphEvent], None]) -> None:
        self._subscribers.append(callback)

    def submit_task_fragment(self, fragment: GraphFragment) -> TaskRecord:
        with self._lock:
            self._validate_fragment(fragment)
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            now = self._clock()
            key_by_artifact = {
                spec.local_id: self._canonical_key(spec.identity, (fragment.task_id,)) if spec.identity is not None else None
                for spec in fragment.artifacts
            }
            fingerprints = [key.fingerprint for key in key_by_artifact.values() if key is not None]
            if len(fingerprints) != len(set(fingerprints)):
                raise GraphError("a task fragment cannot declare the same indexed Artifact twice")

            spec_by_id = {spec.node_id: spec for spec in fragment.work}
            artifact_producers = {artifact.producer_work_id for artifact in fragment.artifacts}
            hit_by_artifact: dict[str, ArtifactNode] = {}
            for artifact in fragment.artifacts:
                key = key_by_artifact[artifact.local_id]
                if key is None:
                    continue
                active = self._active_artifact(record, index, key, artifact.freshness_requirement_seconds, now)
                if active is not None:
                    hit_by_artifact[artifact.local_id] = active

            local_map: dict[str, str] = {}
            referenced_work: set[str] = set()
            referenced_artifacts: set[str] = set()
            for local_id, spec in spec_by_id.items():
                produced = [item for item in fragment.artifacts if item.producer_work_id == local_id]
                if produced and all(item.local_id in hit_by_artifact for item in produced):
                    producer_ids = {hit_by_artifact[item.local_id].producer_work_id for item in produced}
                    if len(producer_ids) != 1:
                        raise GraphError("one local provider cannot map to multiple existing producers")
                    global_id = producer_ids.pop()
                    local_map[local_id] = global_id
                    referenced_work.add(global_id)
                    record.work[global_id] = self._add_membership(record.work[global_id], fragment.task_id)
                    continue
                global_id = self._new_work_id(record)
                local_map[local_id] = global_id
                referenced_work.add(global_id)
                record.work[global_id] = WorkNode(
                    node_id=global_id,
                    agent_id=spec.agent_id,
                    goal=spec.goal,
                    required_resources=spec.required_resources,
                    task_memberships=(fragment.task_id,),
                    metadata=copy.deepcopy(spec.metadata),
                    created_order=self._created_order(record),
                )

            for edge in fragment.control_edges:
                source, target = local_map[edge.from_work_id], local_map[edge.to_work_id]
                self._append_edge(record, DependencyEdge(source, target, EdgeKind.PRECEDES))
                record.work[target] = replace(
                    record.work[target],
                    control_predecessors=self._append_unique(record.work[target].control_predecessors, source),
                )

            for spec in fragment.artifacts:
                consumers = tuple(local_map[item] for item in spec.consumer_work_ids)
                key = key_by_artifact[spec.local_id]
                existing = hit_by_artifact.get(spec.local_id)
                if existing is not None:
                    artifact_id = existing.node_id
                    artifact = self._attach_consumers(record, existing, consumers, fragment.task_id)
                    record.artifacts[artifact_id] = artifact
                    resolution = ResolutionKind.REUSED_CONCRETE if artifact.state is ArtifactState.CONCRETE else ResolutionKind.JOINED_FUTURE
                else:
                    producer_id = local_map[spec.producer_work_id]
                    artifact_id = self._new_artifact_id(record)
                    generation = len(index.history(key)) + 1 if key is not None else 1
                    artifact = ArtifactNode(
                        node_id=artifact_id,
                        kind=spec.kind,
                        state=ArtifactState.FUTURE,
                        producer_work_id=producer_id,
                        consumer_work_ids=consumers,
                        key=key,
                        reuse_policy=ReusePolicy.INDEXED if key is not None else ReusePolicy.UNINDEXED,
                        generation=generation,
                        task_memberships=(fragment.task_id,),
                        created_order=self._created_order(record),
                    )
                    record.artifacts[artifact_id] = artifact
                    record.work[producer_id] = replace(
                        record.work[producer_id],
                        output_artifact_ids=self._append_unique(record.work[producer_id].output_artifact_ids, artifact_id),
                    )
                    self._append_edge(record, DependencyEdge(producer_id, artifact_id, EdgeKind.PRODUCES))
                    if key is not None:
                        index.register(key, artifact_id)
                    resolution = ResolutionKind.CREATED_FUTURE if key is not None else ResolutionKind.CREATED_UNINDEXED
                local_map[spec.local_id] = artifact_id
                referenced_artifacts.add(artifact_id)
                for consumer_id in consumers:
                    requirement = ArtifactRequirement(artifact_id, spec.freshness_requirement_seconds)
                    record.work[consumer_id] = replace(
                        record.work[consumer_id],
                        input_artifacts=self._append_unique(record.work[consumer_id].input_artifacts, requirement),
                    )
                    self._append_edge(record, DependencyEdge(artifact_id, consumer_id, EdgeKind.CONSUMES))
                if resolution in {ResolutionKind.REUSED_CONCRETE, ResolutionKind.JOINED_FUTURE}:
                    referenced_work.add(record.artifacts[artifact_id].producer_work_id)

            entries = self._entry_work_ids(record, referenced_work)
            terminals = self._terminal_work_ids(record, fragment, local_map, referenced_work)
            task = TaskRecord(
                task_id=fragment.task_id,
                user_goal=fragment.user_goal,
                entry_work_ids=entries,
                terminal_work_ids=terminals,
                referenced_work_ids=tuple(sorted(referenced_work, key=lambda item: record.work[item].created_order)),
                referenced_artifact_ids=tuple(sorted(referenced_artifacts, key=lambda item: record.artifacts[item].created_order)),
                local_node_map=tuple(sorted(local_map.items())),
                submitted_at=now,
            )
            record.tasks[fragment.task_id] = task
            self._refresh(record, now)
            event = self._record_event(
                record,
                "task_submitted",
                (fragment.task_id,),
                (*task.referenced_work_ids, *task.referenced_artifact_ids),
            )
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return self.read().task(fragment.task_id)

    def resolve_artifact(
        self,
        identity: ArtifactIdentityCandidate | None,
        consumer_work_id: str,
        task_id: str,
        kind: str,
        producer: WorkSpec,
        freshness_requirement_seconds: float | None = None,
    ) -> ArtifactResolution:
        with self._lock:
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            if consumer_work_id not in record.work or task_id not in record.tasks:
                raise GraphError("artifact resolution refers to unknown work or task")
            if record.work[consumer_work_id].status not in {WorkStatus.BLOCKED, WorkStatus.READY}:
                raise GraphError("direct Artifact resolution requires a non-running consumer WORK")
            key = self._canonical_key(identity, (task_id,)) if identity is not None else None
            now = self._clock()
            existing = self._active_artifact(record, index, key, freshness_requirement_seconds, now) if key else None
            if existing is not None:
                artifact = self._attach_consumers(record, existing, (consumer_work_id,), task_id)
                record.artifacts[artifact.node_id] = artifact
                producer_id = artifact.producer_work_id
                record.work[producer_id] = self._add_membership(record.work[producer_id], task_id)
                resolution = ResolutionKind.REUSED_CONCRETE if artifact.state is ArtifactState.CONCRETE else ResolutionKind.JOINED_FUTURE
            else:
                self._validate_work_spec(producer)
                producer_id = self._new_work_id(record)
                artifact_id = self._new_artifact_id(record)
                record.work[producer_id] = WorkNode(
                    producer_id,
                    producer.agent_id,
                    producer.goal,
                    required_resources=producer.required_resources,
                    task_memberships=(task_id,),
                    metadata=copy.deepcopy(producer.metadata),
                    output_artifact_ids=(artifact_id,),
                    created_order=self._created_order(record),
                )
                artifact = ArtifactNode(
                    artifact_id,
                    kind,
                    ArtifactState.FUTURE,
                    producer_id,
                    (consumer_work_id,),
                    key,
                    ReusePolicy.INDEXED if key is not None else ReusePolicy.UNINDEXED,
                    len(index.history(key)) + 1 if key is not None else 1,
                    task_memberships=(task_id,),
                    created_order=self._created_order(record),
                )
                record.artifacts[artifact_id] = artifact
                self._append_edge(record, DependencyEdge(producer_id, artifact_id, EdgeKind.PRODUCES))
                self._append_edge(record, DependencyEdge(artifact_id, consumer_work_id, EdgeKind.CONSUMES))
                if key is not None:
                    index.register(key, artifact_id)
                resolution = ResolutionKind.CREATED_FUTURE if key is not None else ResolutionKind.CREATED_UNINDEXED
            requirement = ArtifactRequirement(artifact.node_id, freshness_requirement_seconds)
            record.work[consumer_work_id] = replace(
                record.work[consumer_work_id],
                input_artifacts=self._append_unique(record.work[consumer_work_id].input_artifacts, requirement),
            )
            self._append_edge(record, DependencyEdge(artifact.node_id, consumer_work_id, EdgeKind.CONSUMES))
            task = record.tasks[task_id]
            record.tasks[task_id] = replace(
                task,
                referenced_work_ids=self._append_unique(task.referenced_work_ids, producer_id),
                referenced_artifact_ids=self._append_unique(task.referenced_artifact_ids, artifact.node_id),
            )
            self._refresh(record, now)
            event = self._record_event(record, "artifact_resolved", (task_id,), (artifact.node_id, consumer_work_id), {"resolution": resolution})
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return ArtifactResolution(resolution, artifact.node_id, producer_id)

    def read(self, *, include_payloads: bool = True) -> GlobalGraphSnapshot:
        with self._lock:
            return self._snapshot(self._record, include_payloads=include_payloads)

    def read_for_scheduler(self) -> GlobalGraphSnapshot:
        return self.read(include_payloads=False)

    def read_for_work(self, work_id: str) -> GlobalGraphSnapshot:
        with self._lock:
            if work_id not in self._record.work:
                raise GraphError(f"unknown work: {work_id}")
            allowed = {item.artifact_node_id for item in self._record.work[work_id].input_artifacts}
            artifacts = tuple(
                copy.deepcopy(node) if node.node_id in allowed else replace(copy.deepcopy(node), payload=None, evidence_refs=())
                for node in sorted(self._record.artifacts.values(), key=lambda item: item.created_order)
            )
            snapshot = self._snapshot(self._record, include_payloads=False)
            return replace(snapshot, artifact_nodes=artifacts)

    def events(self, task_id: str | None = None) -> tuple[GraphEvent, ...]:
        with self._lock:
            events = self._record.events
            if task_id is not None:
                events = [event for event in events if task_id in event.task_ids]
            return tuple(copy.deepcopy(events))

    def write_events_jsonl(self, path: str | Path, task_id: str | None = None) -> None:
        with Path(path).open("w", encoding="utf-8") as handle:
            for event in self.events(task_id):
                handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def assign(self, work_id: str, assignment_id: str) -> None:
        self._transition_assignment(work_id, assignment_id, WorkStatus.READY, WorkStatus.ASSIGNED, "assignment_created")

    def start(self, work_id: str, assignment_id: str) -> None:
        self._transition_assignment(work_id, assignment_id, WorkStatus.ASSIGNED, WorkStatus.RUNNING, "assignment_started")

    def commit_work(self, work_id: str, assignment_id: str, drafts: tuple[ArtifactDraft, ...] = ()) -> GlobalGraphSnapshot:
        with self._lock:
            current = self._record.work.get(work_id)
            if current is None:
                raise GraphError(f"unknown work: {work_id}")
            if current.status is WorkStatus.DONE and current.assignment_id == assignment_id:
                return self.read()
            self._require_assignment(current, assignment_id, WorkStatus.RUNNING)
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            node = record.work[work_id]
            rejection = self._completion_rejection(record, node, drafts)
            task_ids = node.task_memberships
            if rejection:
                record.work[work_id] = replace(node, status=WorkStatus.FAILED, outcome=rejection)
                self._fail_outputs(record, index, node, rejection)
                event_kind = "completion_rejected"
                detail = {"assignment_id": assignment_id, "reason": rejection}
            else:
                self._concretize_outputs(record, node, drafts)
                report = tuple(copy.deepcopy(draft.payload) for draft in drafts)
                record.work[work_id] = replace(node, status=WorkStatus.DONE, outcome="completed", completion_report=report)
                event_kind = "work_completed"
                detail = {"assignment_id": assignment_id, "artifact_ids": list(node.output_artifact_ids)}
            self._refresh(record, self._clock())
            event = self._record_event(record, event_kind, task_ids, (work_id, *node.output_artifact_ids), detail)
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return self.read()

    def fail_work(self, work_id: str, assignment_id: str, reason: str) -> GlobalGraphSnapshot:
        with self._lock:
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            node = record.work.get(work_id)
            if node is None:
                raise GraphError(f"unknown work: {work_id}")
            self._require_assignment(node, assignment_id, WorkStatus.RUNNING)
            record.work[work_id] = replace(node, status=WorkStatus.FAILED, outcome=reason)
            self._fail_outputs(record, index, node, reason)
            self._refresh(record, self._clock())
            event = self._record_event(record, "work_failed", node.task_memberships, (work_id,), {"reason": reason})
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return self.read()

    def expand_work(self, request: RuntimeExpansion) -> GlobalGraphSnapshot:
        with self._lock:
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            origin = record.work.get(request.origin_work_id)
            if origin is None:
                raise GraphError(f"unknown work: {request.origin_work_id}")
            self._require_assignment(origin, request.assignment_id, WorkStatus.RUNNING)
            providers = self.registry.providers(request.required_capability)
            if request.provider.agent_id not in {item.app_id for item in providers}:
                raise GraphError("runtime expansion provider lacks the required capability")
            task_ids = origin.task_memberships
            if not task_ids:
                raise GraphError("running work has no task membership")
            now = self._clock()

            checkpoint_id = self._new_artifact_id(record)
            checkpoint = ArtifactNode(
                checkpoint_id,
                request.checkpoint.kind,
                ArtifactState.CONCRETE,
                origin.node_id,
                payload=copy.deepcopy(request.checkpoint.payload),
                evidence_refs=request.checkpoint.evidence_refs,
                task_memberships=task_ids,
                observed_at=request.checkpoint.observed_at or now,
                valid_from=now,
                valid_until=request.checkpoint.valid_until,
                source_revision=request.checkpoint.source_revision,
                created_order=self._created_order(record),
            )
            record.artifacts[checkpoint_id] = checkpoint
            self._append_edge(record, DependencyEdge(origin.node_id, checkpoint_id, EdgeKind.PRODUCES))

            continuation_id = self._new_work_id(record)
            continuation = WorkNode(
                continuation_id,
                request.continuation.agent_id,
                request.continuation.goal,
                input_artifacts=(ArtifactRequirement(checkpoint_id),),
                required_resources=request.continuation.required_resources,
                task_memberships=task_ids,
                metadata=copy.deepcopy(request.continuation.metadata),
                created_order=self._created_order(record),
            )
            record.work[continuation_id] = continuation
            record.artifacts[checkpoint_id] = replace(checkpoint, consumer_work_ids=(continuation_id,))
            self._append_edge(record, DependencyEdge(checkpoint_id, continuation_id, EdgeKind.CONSUMES))

            key = self._canonical_key(request.identity, task_ids) if request.identity is not None else None
            existing = self._active_artifact(record, index, key, request.freshness_requirement_seconds, now) if key else None
            if existing is not None:
                requested = self._attach_consumers(record, existing, (continuation_id,), task_ids[0])
                record.artifacts[requested.node_id] = replace(
                    requested,
                    task_memberships=tuple(sorted(set((*requested.task_memberships, *task_ids)))),
                )
                provider_id = requested.producer_work_id
            else:
                provider_id = self._new_work_id(record)
                requested_id = self._new_artifact_id(record)
                record.work[provider_id] = WorkNode(
                    provider_id,
                    request.provider.agent_id,
                    request.provider.goal,
                    output_artifact_ids=(requested_id,),
                    required_resources=request.provider.required_resources,
                    task_memberships=task_ids,
                    metadata=copy.deepcopy(request.provider.metadata),
                    created_order=self._created_order(record),
                )
                requested = ArtifactNode(
                    requested_id,
                    request.artifact_kind,
                    ArtifactState.FUTURE,
                    provider_id,
                    (continuation_id,),
                    key,
                    ReusePolicy.INDEXED if key is not None else ReusePolicy.UNINDEXED,
                    len(index.history(key)) + 1 if key is not None else 1,
                    task_memberships=task_ids,
                    created_order=self._created_order(record),
                )
                record.artifacts[requested_id] = requested
                self._append_edge(record, DependencyEdge(provider_id, requested_id, EdgeKind.PRODUCES))
                if key is not None:
                    index.register(key, requested_id)
            record.work[continuation_id] = replace(
                record.work[continuation_id],
                input_artifacts=(*record.work[continuation_id].input_artifacts, ArtifactRequirement(requested.node_id, request.freshness_requirement_seconds)),
            )
            self._append_edge(record, DependencyEdge(requested.node_id, continuation_id, EdgeKind.CONSUMES))

            rewired: list[DependencyEdge] = []
            for edge in record.edges:
                if edge.kind is EdgeKind.PRECEDES and edge.from_node_id == origin.node_id:
                    successor = record.work[edge.to_node_id]
                    record.work[edge.to_node_id] = replace(
                        successor,
                        control_predecessors=tuple(continuation_id if item == origin.node_id else item for item in successor.control_predecessors),
                    )
                    rewired.append(DependencyEdge(continuation_id, edge.to_node_id, EdgeKind.PRECEDES))
                else:
                    rewired.append(edge)
            record.edges = rewired
            record.work[origin.node_id] = replace(
                origin,
                status=WorkStatus.DONE,
                outcome="checkpoint",
                output_artifact_ids=self._append_unique(origin.output_artifact_ids, checkpoint_id),
                completion_report=(copy.deepcopy(request.checkpoint.payload),),
            )
            self._append_edge(record, DependencyEdge(origin.node_id, continuation_id, EdgeKind.PRECEDES))
            record.work[continuation_id] = replace(
                record.work[continuation_id],
                control_predecessors=self._append_unique(record.work[continuation_id].control_predecessors, origin.node_id),
            )
            for task_id in task_ids:
                task = record.tasks[task_id]
                terminal = tuple(continuation_id if item == origin.node_id else item for item in task.terminal_work_ids)
                record.tasks[task_id] = replace(
                    task,
                    terminal_work_ids=terminal,
                    referenced_work_ids=tuple(sorted(set((*task.referenced_work_ids, provider_id, continuation_id)), key=lambda item: record.work[item].created_order)),
                    referenced_artifact_ids=tuple(sorted(set((*task.referenced_artifact_ids, checkpoint_id, requested.node_id)), key=lambda item: record.artifacts[item].created_order)),
                )
            self._refresh(record, now)
            event = self._record_event(
                record,
                "work_expanded",
                task_ids,
                (origin.node_id, provider_id, requested.node_id, continuation_id),
                {"request_kind": request.request_kind, "required_capability": request.required_capability},
            )
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return self.read()

    def invalidate_artifact(self, artifact_node_id: str, reason: str) -> ArtifactNode:
        with self._lock:
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            artifact = record.artifacts.get(artifact_node_id)
            if artifact is None:
                raise GraphError(f"unknown artifact: {artifact_node_id}")
            if artifact.state not in {ArtifactState.FUTURE, ArtifactState.CONCRETE}:
                raise GraphError(f"artifact is not active: {artifact.state}")
            artifact = replace(artifact, state=ArtifactState.INVALIDATED, failure_reason=reason)
            record.artifacts[artifact_node_id] = artifact
            if artifact.key is not None:
                index.retire(artifact.key, artifact_node_id)
            self._refresh(record, self._clock())
            event = self._record_event(record, "artifact_invalidated", artifact.task_memberships, (artifact_node_id,), {"reason": reason})
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return copy.deepcopy(artifact)

    def sweep_expired(self, now: float | None = None) -> tuple[str, ...]:
        with self._lock:
            current = self._clock() if now is None else now
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            expired = []
            for artifact in record.artifacts.values():
                if artifact.state is ArtifactState.CONCRETE and artifact.valid_until is not None and current > artifact.valid_until:
                    record.artifacts[artifact.node_id] = replace(artifact, state=ArtifactState.INVALIDATED, failure_reason="expired")
                    if artifact.key is not None:
                        index.retire(artifact.key, artifact.node_id)
                    expired.append(artifact.node_id)
            if not expired:
                return ()
            self._refresh(record, current)
            task_ids = tuple(sorted({task for node_id in expired for task in record.artifacts[node_id].task_memberships}))
            event = self._record_event(record, "artifacts_expired", task_ids, tuple(expired))
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return tuple(expired)

    def refresh_readiness(self, now: float | None = None) -> bool:
        """Recheck time-sensitive Artifact requirements before scheduling."""
        with self._lock:
            current = self._clock() if now is None else now
            record, index = copy.deepcopy(self._record), copy.deepcopy(self._artifact_index)
            changed_nodes: set[str] = set()
            for artifact in tuple(record.artifacts.values()):
                if artifact.state is ArtifactState.CONCRETE and artifact.valid_until is not None and current > artifact.valid_until:
                    record.artifacts[artifact.node_id] = replace(artifact, state=ArtifactState.INVALIDATED, failure_reason="expired")
                    if artifact.key is not None:
                        index.retire(artifact.key, artifact.node_id)
                    changed_nodes.add(artifact.node_id)
            before = {node_id: node.status for node_id, node in record.work.items()}
            self._refresh(record, current)
            changed_nodes.update(node_id for node_id, node in record.work.items() if node.status is not before[node_id])
            if not changed_nodes:
                return False
            task_ids = tuple(sorted({task_id for node_id in changed_nodes for task_id in self._node_task_ids(record, node_id)}))
            event = self._record_event(record, "readiness_refreshed", task_ids, tuple(sorted(changed_nodes)))
            self._validate_record(record, index)
            self._record, self._artifact_index = record, index
        self._notify(event)
        return True

    def evaluate_task(self, task_id: str, *, success: bool, outcome: str = "") -> TaskRecord:
        with self._lock:
            record = copy.deepcopy(self._record)
            task = record.tasks.get(task_id)
            if task is None:
                raise GraphError(f"unknown task: {task_id}")
            if success and task.status is not TaskStatus.READY_FOR_EVALUATION:
                raise GraphError("task is not ready for successful evaluation")
            record.tasks[task_id] = replace(task, status=TaskStatus.DONE if success else TaskStatus.FAILED, outcome=outcome)
            event = self._record_event(record, "task_evaluated", (task_id,), task.terminal_work_ids, {"success": success})
            self._validate_record(record, self._artifact_index)
            self._record = record
        self._notify(event)
        return self.read().task(task_id)

    def artifact_by_key(self, key: ArtifactKey) -> ArtifactNode | None:
        with self._lock:
            node_id = self._artifact_index.active(key)
            return copy.deepcopy(self._record.artifacts[node_id]) if node_id else None

    def artifact_history(self, key: ArtifactKey) -> tuple[ArtifactNode, ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._record.artifacts[item]) for item in self._artifact_index.history(key))

    def validate(self) -> None:
        with self._lock:
            self._validate_record(self._record, self._artifact_index)

    def rebuild_artifact_index(self) -> None:
        with self._lock:
            index = ArtifactIndex()
            index.rebuild(self._record.artifacts.values())
            self._validate_record(self._record, index)
            self._artifact_index = index

    def export_checkpoint(self) -> dict[str, object]:
        with self._lock:
            self._validate_record(self._record, self._artifact_index)
            return {
                "version": self._record.version,
                "work": [asdict(item) for item in sorted(self._record.work.values(), key=lambda node: node.created_order)],
                "artifacts": [self._artifact_dict(item) for item in sorted(self._record.artifacts.values(), key=lambda node: node.created_order)],
                "edges": [asdict(item) for item in self._record.edges],
                "tasks": [asdict(item) for item in sorted(self._record.tasks.values(), key=lambda task: task.submitted_at)],
                "events": [asdict(item) for item in self._record.events],
                "next_work": self._record.next_work,
                "next_artifact": self._record.next_artifact,
                "next_created_order": self._record.next_created_order,
                "artifact_index": self._artifact_index.snapshot(),
            }

    def restore_checkpoint(self, checkpoint: dict[str, object]) -> GlobalGraphSnapshot:
        record = self._record_from_checkpoint(checkpoint)
        index = ArtifactIndex()
        index.rebuild(record.artifacts.values())
        if checkpoint.get("artifact_index") != index.snapshot():
            raise GraphError("checkpoint ArtifactIndex does not match graph state")
        self._validate_record(record, index)
        with self._lock:
            if self._record.version or self._record.work or self._record.tasks:
                raise GraphError("restore requires an empty GraphSteward")
            self._record, self._artifact_index = record, index
        return self.read()

    def _transition_assignment(self, work_id: str, assignment_id: str, expected: WorkStatus, target: WorkStatus, event_kind: str) -> None:
        with self._lock:
            record = copy.deepcopy(self._record)
            node = record.work.get(work_id)
            if node is None or node.status is not expected:
                raise GraphError(f"work is not {expected}: {work_id}")
            if expected is WorkStatus.ASSIGNED and node.assignment_id != assignment_id:
                raise GraphError("assignment ownership mismatch")
            record.work[work_id] = replace(node, status=target, assignment_id=assignment_id)
            event = self._record_event(record, event_kind, node.task_memberships, (work_id,), {"assignment_id": assignment_id})
            self._validate_record(record, self._artifact_index)
            self._record = record
        self._notify(event)

    def _canonical_key(self, candidate: ArtifactIdentityCandidate, task_ids: tuple[str, ...]) -> ArtifactKey:
        try:
            schema = self.registry.artifact_schema(candidate.schema_id)
            scope_override = None
            if schema.sharing_scope == "task":
                if len(task_ids) != 1:
                    raise ValueError("task-scoped Artifact identity requires exactly one task membership")
                scope_override = f"task:{task_ids[0]}"
            return schema.canonicalize(candidate, scope_override=scope_override)
        except (KeyError, ValueError) as exc:
            raise GraphError(f"invalid Artifact identity: {exc}") from exc

    def _active_artifact(
        self,
        record: _GlobalGraphRecord,
        index: ArtifactIndex,
        key: ArtifactKey,
        max_age_seconds: float | None,
        now: float,
    ) -> ArtifactNode | None:
        node_id = index.active(key)
        if node_id is None:
            return None
        artifact = record.artifacts[node_id]
        if artifact.state is ArtifactState.FUTURE:
            return artifact
        if self._artifact_satisfies(artifact, max_age_seconds, now):
            return artifact
        record.artifacts[node_id] = replace(artifact, state=ArtifactState.INVALIDATED, failure_reason="stale for new consumer")
        index.retire(key, node_id)
        return None

    @staticmethod
    def _artifact_satisfies(artifact: ArtifactNode, max_age_seconds: float | None, now: float) -> bool:
        if artifact.state is not ArtifactState.CONCRETE:
            return False
        if artifact.valid_from is not None and now < artifact.valid_from:
            return False
        if artifact.valid_until is not None and now > artifact.valid_until:
            return False
        if max_age_seconds is not None:
            if artifact.observed_at is None or now - artifact.observed_at > max_age_seconds:
                return False
        return True

    def _refresh(self, record: _GlobalGraphRecord, now: float) -> None:
        for work_id, node in list(record.work.items()):
            if node.status in {WorkStatus.ASSIGNED, WorkStatus.RUNNING, WorkStatus.DONE, WorkStatus.FAILED}:
                continue
            predecessor_states = [record.work[item].status for item in node.control_predecessors]
            artifacts_ready = all(
                self._artifact_satisfies(record.artifacts[item.artifact_node_id], item.max_age_seconds, now)
                for item in node.input_artifacts
            )
            status = WorkStatus.READY if all(item is WorkStatus.DONE for item in predecessor_states) and artifacts_ready else WorkStatus.BLOCKED
            failed = tuple(item for item in node.control_predecessors if record.work[item].status is WorkStatus.FAILED)
            metadata = copy.deepcopy(node.metadata)
            if failed:
                metadata["blocked_by_failed_predecessors"] = failed
            else:
                metadata.pop("blocked_by_failed_predecessors", None)
            record.work[work_id] = replace(node, status=status, metadata=metadata)
        for task_id, task in list(record.tasks.items()):
            if task.status in {TaskStatus.DONE, TaskStatus.FAILED}:
                continue
            terminals = [record.work[item].status for item in task.terminal_work_ids]
            if terminals and all(item is WorkStatus.DONE for item in terminals):
                status = TaskStatus.READY_FOR_EVALUATION
            elif any(item is WorkStatus.FAILED for item in terminals):
                status = TaskStatus.FAILED
            else:
                status = TaskStatus.ACTIVE
            record.tasks[task_id] = replace(task, status=status)

    def _completion_rejection(self, record: _GlobalGraphRecord, node: WorkNode, drafts: tuple[ArtifactDraft, ...]) -> str | None:
        expected = [record.artifacts[item] for item in node.output_artifact_ids]
        if expected and len(drafts) != len(expected):
            return "completion does not provide every expected Artifact"
        used: set[int] = set()
        for artifact in expected:
            matches = [
                (index, draft)
                for index, draft in enumerate(drafts)
                if index not in used and (draft.artifact_node_id == artifact.node_id or (draft.artifact_node_id is None and draft.kind == artifact.kind))
            ]
            if len(matches) != 1:
                return f"completion cannot uniquely match Artifact {artifact.node_id}"
            index, draft = matches[0]
            used.add(index)
            if artifact.state is not ArtifactState.FUTURE or artifact.producer_work_id != node.node_id:
                return f"Artifact {artifact.node_id} is not publishable by this WORK"
            value, evidence = draft.payload.get("value"), draft.payload.get("evidence")
            if value in (None, "", [], {}):
                return f"Artifact {artifact.node_id} has no result value"
            if not isinstance(evidence, list) or not evidence:
                return f"Artifact {artifact.node_id} has no evidence"
        return None

    def _concretize_outputs(self, record: _GlobalGraphRecord, node: WorkNode, drafts: tuple[ArtifactDraft, ...]) -> None:
        now = self._clock()
        for artifact_id in node.output_artifact_ids:
            artifact = record.artifacts[artifact_id]
            draft = next(
                item for item in drafts
                if item.artifact_node_id == artifact_id or (item.artifact_node_id is None and item.kind == artifact.kind)
            )
            observed_at = draft.observed_at or now
            valid_until = draft.valid_until
            if valid_until is None and artifact.key is not None:
                ttl = self.registry.artifact_schema(artifact.key.schema_id).default_freshness_seconds
                valid_until = observed_at + ttl if ttl is not None else None
            record.artifacts[artifact_id] = replace(
                artifact,
                state=ArtifactState.CONCRETE,
                payload=copy.deepcopy(draft.payload),
                evidence_refs=draft.evidence_refs,
                observed_at=observed_at,
                valid_from=observed_at,
                valid_until=valid_until,
                source_revision=draft.source_revision,
            )

    @staticmethod
    def _fail_outputs(record: _GlobalGraphRecord, index: ArtifactIndex, node: WorkNode, reason: str) -> None:
        for artifact_id in node.output_artifact_ids:
            artifact = record.artifacts[artifact_id]
            if artifact.state is not ArtifactState.FUTURE:
                continue
            record.artifacts[artifact_id] = replace(artifact, state=ArtifactState.FAILED, failure_reason=reason)
            if artifact.key is not None:
                index.retire(artifact.key, artifact_id)

    def _validate_fragment(self, fragment: GraphFragment) -> None:
        if not fragment.task_id.strip() or fragment.task_id in self._record.tasks:
            raise GraphError("task id is empty or already exists")
        if not fragment.work:
            raise GraphError("task fragment contains no WORK")
        ids = [item.node_id for item in fragment.work]
        if len(ids) != len(set(ids)):
            raise GraphError("task fragment contains duplicate local WORK ids")
        for work in fragment.work:
            self._validate_work_spec(work)
        known = set(ids)
        artifact_ids = [item.local_id for item in fragment.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)) or known.intersection(artifact_ids):
            raise GraphError("task fragment contains duplicate local node ids")
        providers = {item.producer_work_id for item in fragment.artifacts}
        control_participants = {item.from_work_id for item in fragment.control_edges} | {item.to_work_id for item in fragment.control_edges}
        if providers.intersection(control_participants):
            raise GraphError("Artifact provider WORK must express handoff through its Artifact")
        producer_counts: dict[str, int] = {}
        for artifact in fragment.artifacts:
            if artifact.producer_work_id not in known or not set(artifact.consumer_work_ids).issubset(known):
                raise GraphError("ArtifactSpec refers to unknown WORK")
            producer_counts[artifact.producer_work_id] = producer_counts.get(artifact.producer_work_id, 0) + 1
        if any(count != 1 for count in producer_counts.values()):
            raise GraphError("one provider WORK must produce one dependency-bearing Artifact")
        for edge in fragment.control_edges:
            if edge.from_work_id not in known or edge.to_work_id not in known:
                raise GraphError("control edge refers to unknown WORK")
        if fragment.terminal_work_ids and not set(fragment.terminal_work_ids).issubset(known):
            raise GraphError("terminal WORK refers to unknown local id")

    def _validate_work_spec(self, work: WorkSpec) -> None:
        if work.agent_id not in {item.app_id for item in self.registry.profiles()}:
            raise GraphError(f"unknown AppAgent: {work.agent_id}")
        if not work.node_id.strip() or not work.goal.strip():
            raise GraphError("WORK requires local id and goal")

    def _validate_record(self, record: _GlobalGraphRecord, index: ArtifactIndex) -> None:
        node_ids = set(record.work) | set(record.artifacts)
        if len(node_ids) != len(record.work) + len(record.artifacts):
            raise GraphError("WORK and Artifact node ids overlap")
        if len(record.edges) != len(set(record.edges)):
            raise GraphError("global graph contains duplicate dependency edges")
        for edge in record.edges:
            if edge.from_node_id not in node_ids or edge.to_node_id not in node_ids:
                raise GraphError("dependency edge has a dangling endpoint")
            if edge.kind is EdgeKind.PRECEDES and (edge.from_node_id not in record.work or edge.to_node_id not in record.work):
                raise GraphError("PRECEDES must connect WORK to WORK")
            if edge.kind is EdgeKind.PRODUCES and (edge.from_node_id not in record.work or edge.to_node_id not in record.artifacts):
                raise GraphError("PRODUCES must connect WORK to Artifact")
            if edge.kind is EdgeKind.CONSUMES and (edge.from_node_id not in record.artifacts or edge.to_node_id not in record.work):
                raise GraphError("CONSUMES must connect Artifact to WORK")
        edge_set = set(record.edges)
        for work in record.work.values():
            if work.agent_id not in {item.app_id for item in self.registry.profiles()}:
                raise GraphError("global graph contains an unknown AppAgent")
            if any(item not in record.work for item in work.control_predecessors):
                raise GraphError("WORK has an unknown control predecessor")
            if any(item.artifact_node_id not in record.artifacts for item in work.input_artifacts):
                raise GraphError("WORK has an unknown Artifact requirement")
            if any(item not in record.artifacts for item in work.output_artifact_ids):
                raise GraphError("WORK has an unknown output Artifact")
            if any(DependencyEdge(item, work.node_id, EdgeKind.PRECEDES) not in edge_set for item in work.control_predecessors):
                raise GraphError("WORK control predecessor lacks PRECEDES edge")
            if any(DependencyEdge(item.artifact_node_id, work.node_id, EdgeKind.CONSUMES) not in edge_set for item in work.input_artifacts):
                raise GraphError("WORK input requirement lacks CONSUMES edge")
            if any(DependencyEdge(work.node_id, item, EdgeKind.PRODUCES) not in edge_set for item in work.output_artifact_ids):
                raise GraphError("WORK output lacks PRODUCES edge")
            if work.status in {WorkStatus.ASSIGNED, WorkStatus.RUNNING} and not work.assignment_id:
                raise GraphError("active WORK lacks assignment ownership")
        for artifact in record.artifacts.values():
            if artifact.producer_work_id not in record.work:
                raise GraphError("Artifact has an unknown producer")
            if any(item not in record.work for item in artifact.consumer_work_ids):
                raise GraphError("Artifact has an unknown consumer")
            if DependencyEdge(artifact.producer_work_id, artifact.node_id, EdgeKind.PRODUCES) not in edge_set:
                raise GraphError("Artifact producer lacks PRODUCES edge")
            if any(DependencyEdge(artifact.node_id, item, EdgeKind.CONSUMES) not in edge_set for item in artifact.consumer_work_ids):
                raise GraphError("Artifact consumer lacks CONSUMES edge")
            if artifact.state is ArtifactState.CONCRETE and artifact.payload is None:
                raise GraphError("CONCRETE Artifact lacks payload")
            if artifact.reuse_policy is ReusePolicy.INDEXED and artifact.key is None:
                raise GraphError("indexed Artifact lacks a key")
        for task in record.tasks.values():
            if any(item not in record.work for item in (*task.entry_work_ids, *task.terminal_work_ids, *task.referenced_work_ids)):
                raise GraphError("TaskRecord refers to unknown WORK")
            if any(item not in record.artifacts for item in task.referenced_artifact_ids):
                raise GraphError("TaskRecord refers to unknown Artifact")
        self._validate_acyclic(record)
        rebuilt = ArtifactIndex()
        try:
            rebuilt.rebuild(record.artifacts.values())
        except ArtifactIndexError as exc:
            raise GraphError(str(exc)) from exc
        if rebuilt.snapshot() != index.snapshot():
            raise GraphError("ArtifactIndex diverges from global graph")
        if record.events and ([item.version for item in record.events] != list(range(1, record.version + 1))):
            raise GraphError("graph event versions are not contiguous")

    @staticmethod
    def _validate_acyclic(record: _GlobalGraphRecord) -> None:
        nodes = set(record.work) | set(record.artifacts)
        successors = {item: [] for item in nodes}
        indegree = {item: 0 for item in nodes}
        for edge in record.edges:
            successors[edge.from_node_id].append(edge.to_node_id)
            indegree[edge.to_node_id] += 1
        ready = [item for item, degree in indegree.items() if degree == 0]
        seen = 0
        while ready:
            current = ready.pop()
            seen += 1
            for successor in successors[current]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
        if seen != len(nodes):
            raise GraphError("global execution graph must remain acyclic")

    @staticmethod
    def _attach_consumers(record: _GlobalGraphRecord, artifact: ArtifactNode, consumers: tuple[str, ...], task_id: str) -> ArtifactNode:
        return replace(
            artifact,
            consumer_work_ids=tuple(sorted(set((*artifact.consumer_work_ids, *consumers)))),
            task_memberships=tuple(sorted(set((*artifact.task_memberships, task_id)))),
        )

    @staticmethod
    def _add_membership(node: WorkNode, task_id: str) -> WorkNode:
        return replace(node, task_memberships=tuple(sorted(set((*node.task_memberships, task_id)))))

    @staticmethod
    def _append_unique(values: tuple, value):
        return values if value in values else (*values, value)

    @staticmethod
    def _append_edge(record: _GlobalGraphRecord, edge: DependencyEdge) -> None:
        if edge not in record.edges:
            record.edges.append(edge)

    @staticmethod
    def _require_assignment(node: WorkNode, assignment_id: str, status: WorkStatus) -> None:
        if node.status is not status or node.assignment_id != assignment_id:
            raise GraphError("assignment ownership or WORK state mismatch")

    @staticmethod
    def _new_work_id(record: _GlobalGraphRecord) -> str:
        node_id = f"W{record.next_work:06d}"
        record.next_work += 1
        return node_id

    @staticmethod
    def _new_artifact_id(record: _GlobalGraphRecord) -> str:
        node_id = f"A{record.next_artifact:06d}"
        record.next_artifact += 1
        return node_id

    @staticmethod
    def _created_order(record: _GlobalGraphRecord) -> int:
        value = record.next_created_order
        record.next_created_order += 1
        return value

    @staticmethod
    def _entry_work_ids(record: _GlobalGraphRecord, work_ids: set[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                (item for item in work_ids if not record.work[item].control_predecessors and not record.work[item].input_artifacts),
                key=lambda item: record.work[item].created_order,
            )
        )

    @staticmethod
    def _terminal_work_ids(record: _GlobalGraphRecord, fragment: GraphFragment, local_map: dict[str, str], work_ids: set[str]) -> tuple[str, ...]:
        if fragment.terminal_work_ids:
            return tuple(local_map[item] for item in fragment.terminal_work_ids)
        outgoing = {
            edge.from_node_id
            for edge in record.edges
            if edge.kind in {EdgeKind.PRECEDES, EdgeKind.PRODUCES} and edge.from_node_id in work_ids
        }
        return tuple(sorted(work_ids - outgoing, key=lambda item: record.work[item].created_order))

    @staticmethod
    def _record_event(
        record: _GlobalGraphRecord,
        kind: str,
        task_ids: tuple[str, ...],
        node_ids: tuple[str, ...],
        detail: dict[str, object] | None = None,
    ) -> GraphEvent:
        record.version += 1
        event = GraphEvent(record.version, kind, tuple(sorted(set(task_ids))), node_ids, dict(detail or {}))
        record.events.append(event)
        return event

    @staticmethod
    def _node_task_ids(record: _GlobalGraphRecord, node_id: str) -> tuple[str, ...]:
        if node_id in record.work:
            return record.work[node_id].task_memberships
        return record.artifacts[node_id].task_memberships

    def _notify(self, event: GraphEvent) -> None:
        for subscriber in tuple(self._subscribers):
            subscriber(event)

    @staticmethod
    def _snapshot(record: _GlobalGraphRecord, *, include_payloads: bool) -> GlobalGraphSnapshot:
        artifacts = tuple(
            copy.deepcopy(node) if include_payloads else replace(copy.deepcopy(node), payload=None, evidence_refs=())
            for node in sorted(record.artifacts.values(), key=lambda item: item.created_order)
        )
        return GlobalGraphSnapshot(
            record.version,
            tuple(copy.deepcopy(node) for node in sorted(record.work.values(), key=lambda item: item.created_order)),
            artifacts,
            tuple(copy.deepcopy(record.edges)),
            tuple(copy.deepcopy(task) for task in sorted(record.tasks.values(), key=lambda item: item.submitted_at)),
        )

    @staticmethod
    def _artifact_dict(artifact: ArtifactNode) -> dict[str, object]:
        value = asdict(artifact)
        value["key"] = artifact.key.to_dict() if artifact.key is not None else None
        return value

    @staticmethod
    def _record_from_checkpoint(checkpoint: dict[str, object]) -> _GlobalGraphRecord:
        work = {
            str(item["node_id"]): WorkNode(
                node_id=str(item["node_id"]),
                agent_id=str(item["agent_id"]),
                goal=str(item["goal"]),
                control_predecessors=tuple(item.get("control_predecessors", ())),
                input_artifacts=tuple(ArtifactRequirement(str(req["artifact_node_id"]), req.get("max_age_seconds")) for req in item.get("input_artifacts", ())),
                output_artifact_ids=tuple(item.get("output_artifact_ids", ())),
                required_resources=tuple(item.get("required_resources", ())),
                task_memberships=tuple(item.get("task_memberships", ())),
                metadata=copy.deepcopy(item.get("metadata", {})),
                status=WorkStatus(str(item["status"])),
                assignment_id=item.get("assignment_id"),
                outcome=item.get("outcome"),
                completion_report=tuple(copy.deepcopy(item.get("completion_report", ()))),
                created_order=int(item.get("created_order", 0)),
            )
            for item in checkpoint.get("work", [])
        }
        artifacts = {
            str(item["node_id"]): ArtifactNode(
                node_id=str(item["node_id"]),
                kind=str(item["kind"]),
                state=ArtifactState(str(item["state"])),
                producer_work_id=str(item["producer_work_id"]),
                consumer_work_ids=tuple(item.get("consumer_work_ids", ())),
                key=ArtifactKey.from_dict(item["key"]) if item.get("key") else None,
                reuse_policy=ReusePolicy(str(item["reuse_policy"])),
                generation=int(item.get("generation", 1)),
                payload=copy.deepcopy(item.get("payload")),
                evidence_refs=tuple(item.get("evidence_refs", ())),
                task_memberships=tuple(item.get("task_memberships", ())),
                observed_at=item.get("observed_at"),
                valid_from=item.get("valid_from"),
                valid_until=item.get("valid_until"),
                source_revision=str(item.get("source_revision", "")),
                failure_reason=item.get("failure_reason"),
                created_order=int(item.get("created_order", 0)),
            )
            for item in checkpoint.get("artifacts", [])
        }
        edges = [DependencyEdge(str(item["from_node_id"]), str(item["to_node_id"]), EdgeKind(str(item["kind"]))) for item in checkpoint.get("edges", [])]
        tasks = {
            str(item["task_id"]): TaskRecord(
                task_id=str(item["task_id"]),
                user_goal=str(item["user_goal"]),
                entry_work_ids=tuple(item.get("entry_work_ids", ())),
                terminal_work_ids=tuple(item.get("terminal_work_ids", ())),
                referenced_work_ids=tuple(item.get("referenced_work_ids", ())),
                referenced_artifact_ids=tuple(item.get("referenced_artifact_ids", ())),
                local_node_map=tuple(tuple(pair) for pair in item.get("local_node_map", ())),
                submitted_at=float(item["submitted_at"]),
                status=TaskStatus(str(item["status"])),
                outcome=item.get("outcome"),
            )
            for item in checkpoint.get("tasks", [])
        }
        events = [
            GraphEvent(int(item["version"]), str(item["kind"]), tuple(item.get("task_ids", ())), tuple(item.get("node_ids", ())), copy.deepcopy(item.get("detail", {})))
            for item in checkpoint.get("events", [])
        ]
        return _GlobalGraphRecord(
            version=int(checkpoint.get("version", 0)),
            work=work,
            artifacts=artifacts,
            edges=edges,
            tasks=tasks,
            events=events,
            next_work=int(checkpoint.get("next_work", len(work) + 1)),
            next_artifact=int(checkpoint.get("next_artifact", len(artifacts) + 1)),
            next_created_order=int(checkpoint.get("next_created_order", len(work) + len(artifacts) + 1)),
        )
