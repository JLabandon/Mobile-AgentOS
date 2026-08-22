from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..graph_space.models import ArtifactDraft, GraphSnapshot, WorkSpec
from ..graph_space.registry import AppProfile
from ..graph_space.steward import CheckpointExpansion, GraphSteward
from ..scheduling.scheduler import Assignment, FifoScheduler


@dataclass(frozen=True)
class ExecutionContext:
    assignment: Assignment
    node_goal: str
    snapshot: GraphSnapshot
    profile: AppProfile
    input_artifacts: tuple[object, ...]


@dataclass(frozen=True)
class Completed:
    artifacts: tuple[ArtifactDraft, ...] = ()


@dataclass(frozen=True)
class Failed:
    reason: str


@dataclass(frozen=True)
class NeedsExpansion:
    checkpoint: ArtifactDraft
    provider_agent_id: str
    required_capability: str
    provider_goal: str
    provider_artifact_kinds: tuple[str, ...]
    continuation_goal: str
    request_kind: str = "information"


ExecutionResult = Completed | Failed | NeedsExpansion


class PrimitiveExecutor(Protocol):
    def execute(self, context: ExecutionContext) -> ExecutionResult:
        ...


class AppAgent:
    """An assignment-driven AppAgent service. It owns no graph mutation rights."""

    def __init__(self, agent_id: str, steward: GraphSteward, scheduler: FifoScheduler, executor: PrimitiveExecutor) -> None:
        self.agent_id = agent_id
        self.steward = steward
        self.scheduler = scheduler
        self.executor = executor

    def run_once(self) -> bool:
        assignments = self.scheduler.assignments_for(self.agent_id)
        if not assignments:
            return False
        assignment = self.scheduler.claim(assignments[0].assignment_id, self.agent_id)
        snapshot = self.steward.read_for_node(assignment.graph_id, assignment.node_id)
        node = snapshot.node(assignment.node_id)
        context = ExecutionContext(
            assignment=assignment,
            node_goal=node.goal,
            snapshot=snapshot,
            profile=self.steward.registry.get(self.agent_id),
            input_artifacts=snapshot.input_artifacts(node.node_id),
        )
        try:
            result = self.executor.execute(context)
        except Exception as exc:
            self.steward.fail_node(
                assignment.graph_id,
                assignment.node_id,
                assignment.assignment_id,
                f"executor error: {type(exc).__name__}: {exc}",
            )
            self.scheduler.schedule(assignment.graph_id)
            return True
        if isinstance(result, Completed):
            self.steward.commit_node(assignment.graph_id, assignment.node_id, assignment.assignment_id, result.artifacts)
        elif isinstance(result, Failed):
            self.steward.fail_node(assignment.graph_id, assignment.node_id, assignment.assignment_id, result.reason)
        else:
            provider = WorkSpec(
                node_id=f"{assignment.node_id}_provider",
                agent_id=result.provider_agent_id,
                goal=result.provider_goal,
                expected_artifact_kinds=result.provider_artifact_kinds,
            )
            continuation = WorkSpec(
                node_id=f"{assignment.node_id}_continuation",
                agent_id=self.agent_id,
                goal=result.continuation_goal,
            )
            self.steward.checkpoint_and_expand(
                assignment.graph_id,
                CheckpointExpansion(
                    origin_node_id=assignment.node_id,
                    assignment_id=assignment.assignment_id,
                    checkpoint=result.checkpoint,
                    provider=provider,
                    continuation=continuation,
                    request_kind=result.request_kind,
                    required_capability=result.required_capability,
                ),
            )
        self.scheduler.schedule(assignment.graph_id)
        return True
