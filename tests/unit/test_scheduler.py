from mobile_agent_os.graph_space import (
    AppProfile,
    ArtifactDraft,
    ArtifactIdentityCandidate,
    ArtifactSpec,
    ControlEdgeSpec,
    GraphFragment,
    GraphSteward,
    RegistryTable,
    WorkSpec,
    WorkStatus,
)
from mobile_agent_os.scheduling import CriticalPathPolicy, FanoutPolicy, GraphScheduler, HybridPolicy, ResourceTable

from tests.fakes.runtime import registry


def test_scheduler_assigns_independent_ready_work() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable())
    task = steward.submit_task_fragment(
        GraphFragment(
            "parallel",
            "Complete two independent jobs.",
            (WorkSpec("calendar", "calendar", "Create event"), WorkSpec("notes", "notes", "Find note")),
            terminal_work_ids=("calendar", "notes"),
        )
    )
    assert {item.work_id for item in (*scheduler.assignments_for("calendar"), *scheduler.assignments_for("notes"))} == {
        task.global_id("calendar"),
        task.global_id("notes"),
    }


def test_single_app_service_queues_second_work_until_completion() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable())
    steward.submit_task_fragment(
        GraphFragment(
            "queue",
            "Read two independent records.",
            (WorkSpec("first", "notes", "Find first"), WorkSpec("second", "notes", "Find second")),
            terminal_work_ids=("first", "second"),
        )
    )
    first = scheduler.assignments_for("notes")
    assert len(first) == 1
    claim = scheduler.claim(first[0].assignment_id, "notes")
    steward.commit_work(claim.work_id, claim.assignment_id)
    second = scheduler.assignments_for("notes")
    assert len(second) == 1
    assert second[0].work_id != claim.work_id


def test_join_releases_only_after_both_control_predecessors_finish() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable())
    task = steward.submit_task_fragment(
        GraphFragment(
            "join",
            "Complete two branches and then join.",
            (
                WorkSpec("left", "calendar", "Left branch"),
                WorkSpec("right", "notes", "Right branch"),
                WorkSpec("join", "payment", "Join work"),
            ),
            (ControlEdgeSpec("left", "join"), ControlEdgeSpec("right", "join")),
            terminal_work_ids=("join",),
        )
    )
    left = scheduler.claim(scheduler.assignments_for("calendar")[0].assignment_id, "calendar")
    steward.commit_work(left.work_id, left.assignment_id)
    assert steward.read().work(task.global_id("join")).status is WorkStatus.BLOCKED
    right = scheduler.claim(scheduler.assignments_for("notes")[0].assignment_id, "notes")
    steward.commit_work(right.work_id, right.assignment_id)
    assert steward.read().work(task.global_id("join")).status is WorkStatus.ASSIGNED


def test_scheduler_leases_profile_default_resource() -> None:
    table = RegistryTable(
        {"calendar": AppProfile("calendar", "Calendar", "Appointment app", ("create_event",), ("calendar.pkg",), default_resources=("foreground_display:primary",))}
    )
    steward = GraphSteward(table)
    scheduler = GraphScheduler(steward, ResourceTable())
    steward.submit_task_fragment(GraphFragment("resource", "Create event.", (WorkSpec("work", "calendar", "Create event"),), terminal_work_ids=("work",)))
    assert scheduler.resources.snapshot()["foreground_display:primary"]["leased"] == 1


def test_critical_path_policy_prioritizes_longer_remaining_path() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable(), CriticalPathPolicy())
    task = steward.submit_task_fragment(
        GraphFragment(
            "critical",
            "Run competing paths.",
            (
                WorkSpec("short", "notes", "Short", metadata={"estimated_duration": 1}),
                WorkSpec("long", "notes", "Long start", metadata={"estimated_duration": 1}),
                WorkSpec("tail", "calendar", "Long tail", metadata={"estimated_duration": 10}),
            ),
            (ControlEdgeSpec("long", "tail"),),
            terminal_work_ids=("short", "tail"),
        )
    )
    assert scheduler.assignments_for("notes")[0].work_id == task.global_id("long")


def test_fanout_policy_counts_consumers_through_artifact_nodes() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable(), FanoutPolicy())
    task = steward.submit_task_fragment(
        GraphFragment(
            "fanout",
            "Compare a leaf and shared provider.",
            (
                WorkSpec("leaf", "notes", "Leaf"),
                WorkSpec("provider", "notes", "Shared provider"),
                WorkSpec("c1", "calendar", "Consumer one"),
                WorkSpec("c2", "payment", "Consumer two"),
            ),
            artifacts=(ArtifactSpec("shared", "shared_result", "provider", ("c1", "c2")),),
            terminal_work_ids=("leaf", "c1", "c2"),
        )
    )
    assert scheduler.assignments_for("notes")[0].work_id == task.global_id("provider")


def test_hybrid_policy_reads_one_global_snapshot_without_mutating_it() -> None:
    steward = GraphSteward(registry())
    policy = HybridPolicy(critical_path_weight=1.0, fanout_weight=2.0, duration_weight=0.1)
    scheduler = GraphScheduler(steward, ResourceTable(), policy)
    task = steward.submit_task_fragment(
        GraphFragment(
            "hybrid",
            "Compare two ready jobs.",
            (
                WorkSpec("a", "notes", "Independent", metadata={"estimated_duration": 1}),
                WorkSpec("b", "notes", "Unblock consumer", metadata={"estimated_duration": 2}),
                WorkSpec("c", "calendar", "Consumer", metadata={"estimated_duration": 2}),
            ),
            (ControlEdgeSpec("b", "c"),),
            terminal_work_ids=("a", "c"),
        )
    )
    before = steward.read_for_scheduler().edges
    assert scheduler.assignments_for("notes")[0].work_id == task.global_id("b")
    assert steward.read_for_scheduler().edges == before


def test_scheduler_rechecks_artifact_freshness_before_assignment() -> None:
    now = [100.0]
    steward = GraphSteward(registry(), clock=lambda: now[0])
    task = steward.submit_task_fragment(
        GraphFragment(
            "freshness",
            "Use a time-sensitive code.",
            (WorkSpec("provider", "notes", "Retrieve code"), WorkSpec("consumer", "calendar", "Use code")),
            artifacts=(
                ArtifactSpec(
                    "code",
                    "project_code",
                    "provider",
                    ("consumer",),
                    ArtifactIdentityCandidate("project.code", {"project": "Project Alpha"}),
                    freshness_requirement_seconds=10,
                ),
            ),
            terminal_work_ids=("consumer",),
        )
    )
    provider, artifact = task.global_id("provider"), task.global_id("code")
    steward.assign(provider, "AS-P")
    steward.start(provider, "AS-P")
    steward.commit_work(
        provider,
        "AS-P",
        (ArtifactDraft("project_code", {"value": "ALPHA-42", "evidence": ["visible note"]}, artifact_node_id=artifact),),
    )
    assert steward.read().work(task.global_id("consumer")).status is WorkStatus.READY
    now[0] = 111.0
    scheduler = GraphScheduler(steward, ResourceTable())
    assert scheduler.schedule() == ()
    assert steward.read().work(task.global_id("consumer")).status is WorkStatus.BLOCKED
