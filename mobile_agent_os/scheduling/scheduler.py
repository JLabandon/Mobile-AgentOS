from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from ..graph_space.models import GraphEvent, NodeKind, NodeStatus
from ..graph_space.steward import GraphSteward
from .policy import FifoPolicy, SchedulingPolicy
from .resources import ResourceTable


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    graph_id: str
    node_id: str
    agent_id: str
    graph_version: int
    lease_ids: tuple[str, ...]


class FifoScheduler:
    """Kahn-style topological scheduling with FIFO tie-breaking and hard resource admission."""

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
        if event.kind in {"graph_created", "node_completed", "node_failed", "graph_expanded", "sink_evaluated"}:
            self.schedule(event.graph_id)

    def schedule(self, graph_id: str) -> tuple[Assignment, ...]:
        with self._lock:
            self._release_terminal_assignments(graph_id)
            snapshot = self.steward.read_for_scheduler(graph_id)
            ready = self.policy.order(snapshot.ready_work_nodes())
            created: list[Assignment] = []
            for node in ready:
                assert node.agent_id is not None
                profile = self.steward.registry.get(node.agent_id)
                requirements = (f"app_service:{node.agent_id}", *profile.default_resources, *node.required_resources)
                assignment_id = f"AS-{uuid4().hex[:10]}"
                leases = self.resources.try_acquire(assignment_id, requirements)
                if leases is None:
                    continue
                self.steward.assign(graph_id, node.node_id, assignment_id)
                assigned_version = self.steward.read_for_scheduler(graph_id).version
                assignment = Assignment(assignment_id, graph_id, node.node_id, node.agent_id, assigned_version, tuple(lease.lease_id for lease in leases))
                self._assignments[assignment_id] = assignment
                created.append(assignment)
            return tuple(created)

    def claim(self, assignment_id: str, agent_id: str) -> Assignment:
        with self._lock:
            assignment = self._assignments[assignment_id]
            if assignment.agent_id != agent_id:
                raise PermissionError(f"assignment {assignment_id} belongs to {assignment.agent_id}")
            self.steward.start(assignment.graph_id, assignment.node_id, assignment.assignment_id)
            return assignment

    def assignments_for(self, agent_id: str) -> tuple[Assignment, ...]:
        snapshot_by_graph: dict[str, object] = {}
        pending = []
        for assignment in self._assignments.values():
            if assignment.agent_id != agent_id:
                continue
            snapshot = snapshot_by_graph.setdefault(assignment.graph_id, self.steward.read_for_scheduler(assignment.graph_id))
            if snapshot.node(assignment.node_id).status is NodeStatus.ASSIGNED:
                pending.append(assignment)
        return tuple(sorted(pending, key=lambda item: item.assignment_id))

    def _release_terminal_assignments(self, graph_id: str) -> None:
        snapshot = self.steward.read_for_scheduler(graph_id)
        terminal = {NodeStatus.DONE, NodeStatus.FAILED}
        for assignment_id, assignment in list(self._assignments.items()):
            if assignment.graph_id != graph_id:
                continue
            if snapshot.node(assignment.node_id).status in terminal:
                self.resources.release_owner(assignment_id)
                del self._assignments[assignment_id]
