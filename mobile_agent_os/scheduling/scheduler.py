from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from ..graph_space.schema import GraphEvent, WorkStatus
from ..graph_space.steward import GraphSteward
from .policy import FifoPolicy, SchedulingCandidate, SchedulingPolicy
from .resources import ResourceTable


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    work_id: str
    agent_id: str
    graph_version: int
    task_ids: tuple[str, ...]
    lease_ids: tuple[str, ...]


class GraphScheduler:
    """Orders the global ready frontier and admits WORK under hard resources."""

    def __init__(self, steward: GraphSteward, resources: ResourceTable, policy: SchedulingPolicy | None = None) -> None:
        self.steward = steward
        self.resources = resources
        self.policy = policy or FifoPolicy()
        self._assignments: dict[str, Assignment] = {}
        self._lock = RLock()
        steward.subscribe(self.on_graph_event)
        for profile in steward.registry.profiles():
            self.resources.ensure(f"app_service:{profile.app_id}", profile.service_capacity)

    def on_graph_event(self, event: GraphEvent) -> None:
        if event.kind in {
            "task_submitted",
            "work_completed",
            "work_failed",
            "completion_rejected",
            "work_expanded",
            "artifact_invalidated",
            "artifacts_expired",
        }:
            self.schedule()

    def schedule(self) -> tuple[Assignment, ...]:
        with self._lock:
            self._release_terminal_assignments()
            self.steward.refresh_readiness()
            snapshot = self.steward.read_for_scheduler()
            ordered = self.policy.order(tuple(SchedulingCandidate(snapshot, work) for work in snapshot.ready_work_nodes()))
            created: list[Assignment] = []
            for candidate in ordered:
                work = candidate.work
                profile = self.steward.registry.get(work.agent_id)
                requirements = (f"app_service:{work.agent_id}", *profile.default_resources, *work.required_resources)
                assignment_id = f"AS-{uuid4().hex[:10]}"
                leases = self.resources.try_acquire(assignment_id, requirements)
                if leases is None:
                    continue
                try:
                    self.steward.assign(work.node_id, assignment_id)
                except Exception:
                    self.resources.release_owner(assignment_id)
                    raise
                assigned = self.steward.read_for_scheduler().work(work.node_id)
                assignment = Assignment(
                    assignment_id,
                    work.node_id,
                    work.agent_id,
                    self.steward.read_for_scheduler().version,
                    assigned.task_memberships,
                    tuple(lease.lease_id for lease in leases),
                )
                self._assignments[assignment_id] = assignment
                created.append(assignment)
            return tuple(created)

    def claim(self, assignment_id: str, agent_id: str) -> Assignment:
        with self._lock:
            assignment = self._assignments[assignment_id]
            if assignment.agent_id != agent_id:
                raise PermissionError(f"assignment {assignment_id} belongs to {assignment.agent_id}")
            self.steward.start(assignment.work_id, assignment.assignment_id)
            return assignment

    def assignments_for(self, agent_id: str) -> tuple[Assignment, ...]:
        with self._lock:
            snapshot = self.steward.read_for_scheduler()
            pending = [
                assignment
                for assignment in self._assignments.values()
                if assignment.agent_id == agent_id and snapshot.work(assignment.work_id).status is WorkStatus.ASSIGNED
            ]
            return tuple(sorted(pending, key=lambda item: snapshot.work(item.work_id).created_order))

    def _release_terminal_assignments(self) -> None:
        snapshot = self.steward.read_for_scheduler()
        for assignment_id, assignment in list(self._assignments.items()):
            if snapshot.work(assignment.work_id).status in {WorkStatus.DONE, WorkStatus.FAILED}:
                self.resources.release_owner(assignment_id)
                del self._assignments[assignment_id]
