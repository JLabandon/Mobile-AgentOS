import json

import pytest

from mobile_agent_os.execution import AppAgent, Completed
from mobile_agent_os.graph_space import (
    ArtifactDraft,
    ArtifactIdentityCandidate,
    ArtifactSpec,
    ArtifactState,
    ControlEdgeSpec,
    GraphFragment,
    GraphSteward,
    ReusePolicy,
    RuntimeExpansion,
    TaskStatus,
    WorkSpec,
    WorkStatus,
)
from mobile_agent_os.graph_space.steward import GraphError
from mobile_agent_os.scheduling import GraphScheduler, ResourceTable

from tests.fakes.runtime import registry


class _CompletingExecutor:
    def __init__(self) -> None:
        self.work_ids: list[str] = []

    def execute(self, context):
        self.work_ids.append(context.assignment.work_id)
        work = context.snapshot.work(context.assignment.work_id)
        return Completed(
            tuple(
                ArtifactDraft(
                    context.snapshot.artifact(artifact_id).kind,
                    {"value": "simulated result", "evidence": ["simulated visible state"]},
                    artifact_node_id=artifact_id,
                )
                for artifact_id in work.output_artifact_ids
            )
        )


def _linear(task_id: str = "task-a") -> GraphFragment:
    return GraphFragment(
        task_id,
        "Complete a two-step appointment workflow.",
        (WorkSpec("first", "calendar", "Start appointment"), WorkSpec("second", "calendar", "Finish appointment")),
        (ControlEdgeSpec("first", "second"),),
        terminal_work_ids=("second",),
    )


def _start(steward: GraphSteward, task_id: str, local_id: str, assignment_id: str = "AS-1") -> str:
    work_id = steward.read().task(task_id).global_id(local_id)
    steward.assign(work_id, assignment_id)
    steward.start(work_id, assignment_id)
    return work_id


def test_task_fragment_joins_the_single_global_graph() -> None:
    steward = GraphSteward(registry())
    first = steward.submit_task_fragment(_linear("task-a"))
    second = steward.submit_task_fragment(
        GraphFragment("task-b", "Independent work.", (WorkSpec("only", "notes", "Read a note"),), terminal_work_ids=("only",))
    )
    snapshot = steward.read()
    assert snapshot.version == 2
    assert snapshot.work(first.global_id("first")).status is WorkStatus.READY
    assert snapshot.work(first.global_id("second")).status is WorkStatus.BLOCKED
    assert snapshot.work(second.global_id("only")).status is WorkStatus.READY
    assert {task.task_id for task in snapshot.tasks} == {"task-a", "task-b"}


def test_work_artifact_work_dataflow_releases_consumer() -> None:
    steward = GraphSteward(registry())
    task = steward.submit_task_fragment(
        GraphFragment(
            "appointment",
            "Create an appointment using stored details.",
            (WorkSpec("fetch", "notes", "Retrieve appointment details"), WorkSpec("create", "calendar", "Create appointment")),
            artifacts=(ArtifactSpec("details", "appointment_details", "fetch", ("create",)),),
            terminal_work_ids=("create",),
        )
    )
    fetch = task.global_id("fetch")
    create = task.global_id("create")
    artifact = task.global_id("details")
    assert steward.read().work(fetch).status is WorkStatus.READY
    assert steward.read().work(create).status is WorkStatus.BLOCKED
    steward.assign(fetch, "AS-F")
    steward.start(fetch, "AS-F")
    steward.commit_work(
        fetch,
        "AS-F",
        (ArtifactDraft("appointment_details", {"value": "Room 301", "evidence": ["Visible appointment note"]}, artifact_node_id=artifact),),
    )
    snapshot = steward.read()
    assert snapshot.artifact(artifact).state is ArtifactState.CONCRETE
    assert snapshot.work(create).status is WorkStatus.READY


def test_scheduler_and_unrelated_work_cannot_read_artifact_payload() -> None:
    steward = GraphSteward(registry())
    task = steward.submit_task_fragment(
        GraphFragment(
            "isolation",
            "Use one private note while doing unrelated work.",
            (
                WorkSpec("producer", "notes", "Read private note"),
                WorkSpec("consumer", "calendar", "Use private note"),
                WorkSpec("unrelated", "payment", "Inspect payment"),
            ),
            artifacts=(ArtifactSpec("private", "private_note", "producer", ("consumer",)),),
            terminal_work_ids=("consumer", "unrelated"),
        )
    )
    producer = task.global_id("producer")
    artifact = task.global_id("private")
    steward.assign(producer, "AS-P")
    steward.start(producer, "AS-P")
    steward.commit_work(
        producer,
        "AS-P",
        (ArtifactDraft("private_note", {"value": "secret", "evidence": ["visible note"]}, artifact_node_id=artifact),),
    )
    assert steward.read_for_scheduler().artifact(artifact).payload is None
    assert steward.read_for_work(task.global_id("unrelated")).artifact(artifact).payload is None
    assert steward.read_for_work(task.global_id("consumer")).artifact(artifact).payload["value"] == "secret"


def test_runtime_expansion_adds_checkpoint_provider_artifact_and_continuation() -> None:
    steward = GraphSteward(registry())
    task = steward.submit_task_fragment(_linear("runtime"))
    origin = _start(steward, "runtime", "first", "AS-A")
    snapshot = steward.expand_work(
        RuntimeExpansion(
            origin,
            "AS-A",
            ArtifactDraft("execution_checkpoint", {"value": "form open", "evidence": ["visible form"]}),
            WorkSpec("provider", "notes", "Find appointment location"),
            WorkSpec("continuation", "calendar", "Continue appointment"),
            "appointment_location",
            ArtifactIdentityCandidate("appointment.location", {"participant": "Alice", "start_time": "2026-08-26T15:00:00+08:00"}),
            "information",
            "search_notes",
        )
    )
    continuation = next(item for item in snapshot.work_nodes if item.goal == "Continue appointment")
    provider = next(item for item in snapshot.work_nodes if item.goal == "Find appointment location")
    assert snapshot.work(origin).status is WorkStatus.DONE
    assert provider.status is WorkStatus.READY
    assert continuation.status is WorkStatus.BLOCKED
    assert len(continuation.input_artifacts) == 2
    assert snapshot.task("runtime").terminal_work_ids == (task.global_id("second"),)
    assert continuation.node_id in snapshot.work(task.global_id("second")).control_predecessors


def test_completion_gate_rejects_empty_required_artifact() -> None:
    steward = GraphSteward(registry())
    task = steward.submit_task_fragment(
        GraphFragment(
            "gate",
            "Retrieve a code.",
            (WorkSpec("provider", "notes", "Retrieve code"),),
            artifacts=(ArtifactSpec("code", "project_code", "provider", ()),),
            terminal_work_ids=("provider",),
        )
    )
    provider = _start(steward, "gate", "provider")
    artifact = task.global_id("code")
    snapshot = steward.commit_work(provider, "AS-1", (ArtifactDraft("project_code", {}, artifact_node_id=artifact),))
    assert snapshot.work(provider).status is WorkStatus.FAILED
    assert snapshot.artifact(artifact).state is ArtifactState.FAILED
    assert snapshot.artifact(artifact).payload is None


def test_checkpoint_round_trip_restores_global_tasks_and_index() -> None:
    steward = GraphSteward(registry())
    task = steward.submit_task_fragment(
        GraphFragment(
            "checkpoint",
            "Retrieve a project code.",
            (WorkSpec("provider", "notes", "Retrieve project code"),),
            artifacts=(
                ArtifactSpec(
                    "code",
                    "project_code",
                    "provider",
                    (),
                    ArtifactIdentityCandidate("project.code", {"project": "Project Alpha"}),
                ),
            ),
            terminal_work_ids=("provider",),
        )
    )
    provider = _start(steward, "checkpoint", "provider")
    artifact = task.global_id("code")
    steward.commit_work(
        provider,
        "AS-1",
        (ArtifactDraft("project_code", {"value": "ALPHA-42", "evidence": ["visible note"]}, artifact_node_id=artifact),),
    )
    checkpoint = json.loads(json.dumps(steward.export_checkpoint()))
    restored = GraphSteward(registry())
    snapshot = restored.restore_checkpoint(checkpoint)
    restored.validate()
    assert snapshot.task("checkpoint").status is TaskStatus.READY_FOR_EVALUATION
    assert snapshot.artifact(artifact).payload["value"] == "ALPHA-42"
    key = registry().artifact_schema("project.code").canonicalize(
        ArtifactIdentityCandidate("project.code", {"project": "project alpha"})
    )
    assert restored.artifact_by_key(key).node_id == artifact


def test_checkpoint_rejects_index_that_disagrees_with_graph() -> None:
    steward = GraphSteward(registry())
    steward.submit_task_fragment(_linear())
    checkpoint = steward.export_checkpoint()
    checkpoint["artifact_index"] = {"active": {"bad": "A999999"}, "history": {}}
    restored = GraphSteward(registry())
    with pytest.raises(GraphError, match="ArtifactIndex"):
        restored.restore_checkpoint(checkpoint)


def test_checkpoint_rejects_dependency_edge_with_wrong_node_types() -> None:
    steward = GraphSteward(registry())
    steward.submit_task_fragment(_linear())
    checkpoint = steward.export_checkpoint()
    checkpoint["edges"][0]["kind"] = "PRODUCES"
    restored = GraphSteward(registry())
    with pytest.raises(GraphError, match="PRODUCES"):
        restored.restore_checkpoint(checkpoint)


def test_control_cycle_is_rejected_without_publishing_partial_task() -> None:
    steward = GraphSteward(registry())
    with pytest.raises(GraphError, match="acyclic"):
        steward.submit_task_fragment(
            GraphFragment(
                "cycle",
                "Cycle fixture.",
                (WorkSpec("a", "notes", "A"), WorkSpec("b", "calendar", "B")),
                (ControlEdgeSpec("a", "b"), ControlEdgeSpec("b", "a")),
            )
        )
    assert steward.read().tasks == ()


def test_unindexed_artifact_is_task_scoped() -> None:
    steward = GraphSteward(registry())
    task = steward.submit_task_fragment(
        GraphFragment(
            "private",
            "Produce a private result.",
            (WorkSpec("p", "notes", "Read private result"),),
            artifacts=(ArtifactSpec("a", "private_result", "p", ()),),
            terminal_work_ids=("p",),
        )
    )
    artifact = steward.read().artifact(task.global_id("a"))
    assert artifact.key is None
    assert artifact.reuse_policy is ReusePolicy.UNINDEXED


def test_full_runtime_executes_one_shared_provider_for_two_tasks() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable())

    def fragment(task_id: str, consumer_goal: str) -> GraphFragment:
        return GraphFragment(
            task_id,
            consumer_goal,
            (WorkSpec("provider", "notes", "Retrieve project code"), WorkSpec("consumer", "calendar", consumer_goal)),
            artifacts=(
                ArtifactSpec(
                    "code",
                    "project_code",
                    "provider",
                    ("consumer",),
                    ArtifactIdentityCandidate("project.code", {"project": "Project Alpha"}),
                ),
            ),
            terminal_work_ids=("consumer",),
        )

    first = steward.submit_task_fragment(fragment("task-a", "Create Alpha review"))
    second = steward.submit_task_fragment(fragment("task-b", "Create Alpha handoff"))
    executor = _CompletingExecutor()
    agents = {
        app_id: AppAgent(app_id, steward, scheduler, executor)
        for app_id in ("notes", "calendar")
    }
    for _ in range(6):
        progressed = any(tuple(agent.run_once() for agent in agents.values()))
        if all(steward.read().task(task_id).status is TaskStatus.READY_FOR_EVALUATION for task_id in ("task-a", "task-b")):
            break
        assert progressed

    assert executor.work_ids.count(first.global_id("provider")) == 1
    assert first.global_id("provider") == second.global_id("provider")
    assert steward.read().task("task-a").status is TaskStatus.READY_FOR_EVALUATION
    assert steward.read().task("task-b").status is TaskStatus.READY_FOR_EVALUATION
