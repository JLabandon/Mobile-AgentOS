from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..graph_space.registry import AppProfile
from ..graph_space.schema import ArtifactDraft, ArtifactIdentityCandidate, ArtifactNode, GlobalGraphSnapshot, WorkSpec
from ..graph_space.steward import GraphSteward, RuntimeExpansion
from ..scheduling.scheduler import Assignment, GraphScheduler


@dataclass(frozen=True)
class ExecutionContext:
    assignment: Assignment
    work_goal: str
    snapshot: GlobalGraphSnapshot
    profile: AppProfile
    input_artifacts: tuple[ArtifactNode, ...]


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
    artifact_kind: str
    continuation_goal: str
    identity: ArtifactIdentityCandidate | None = None
    freshness_requirement_seconds: float | None = None
    request_kind: str = "information"


ExecutionResult = Completed | Failed | NeedsExpansion


class PrimitiveExecutor(Protocol):
    def execute(self, context: ExecutionContext) -> ExecutionResult:
        ...


class AppAgent:
    """Assignment-driven AppAgent service with no direct graph mutation rights."""

    def __init__(self, agent_id: str, steward: GraphSteward, scheduler: GraphScheduler, executor: PrimitiveExecutor) -> None:
        self.agent_id = agent_id
        self.steward = steward
        self.scheduler = scheduler
        self.executor = executor

    def run_once(self) -> bool:
        assignments = self.scheduler.assignments_for(self.agent_id)
        if not assignments:
            return False
        assignment = self.scheduler.claim(assignments[0].assignment_id, self.agent_id)
        snapshot = self.steward.read_for_work(assignment.work_id)
        work = snapshot.work(assignment.work_id)
        context = ExecutionContext(
            assignment=assignment,
            work_goal=work.goal,
            snapshot=snapshot,
            profile=self.steward.registry.get(self.agent_id),
            input_artifacts=snapshot.input_artifact_nodes(work.node_id),
        )
        try:
            result = self.executor.execute(context)
        except Exception as exc:
            self.steward.fail_work(
                assignment.work_id,
                assignment.assignment_id,
                f"executor error: {type(exc).__name__}: {exc}",
            )
            return True
        if isinstance(result, Completed):
            self.steward.commit_work(assignment.work_id, assignment.assignment_id, result.artifacts)
        elif isinstance(result, Failed):
            self.steward.fail_work(assignment.work_id, assignment.assignment_id, result.reason)
        else:
            self.steward.expand_work(
                RuntimeExpansion(
                    origin_work_id=assignment.work_id,
                    assignment_id=assignment.assignment_id,
                    checkpoint=result.checkpoint,
                    provider=WorkSpec("provider", result.provider_agent_id, result.provider_goal),
                    continuation=WorkSpec("continuation", self.agent_id, result.continuation_goal),
                    artifact_kind=result.artifact_kind,
                    identity=result.identity,
                    request_kind=result.request_kind,
                    required_capability=result.required_capability,
                    freshness_requirement_seconds=result.freshness_requirement_seconds,
                )
            )
        return True
