from mobile_agent_os.graph_space import AppProfile, ArtifactDraft, ArtifactKey, Edge, GraphSteward, InitialGraph, NodeStatus, RegistryTable, WorkSpec
from mobile_agent_os.scheduling import CriticalPathPolicy, FanoutPolicy, GraphScheduler, HybridPolicy, ResourceTable

from tests.fakes.runtime import registry


def test_fifo_scheduler_assigns_independent_work_with_independent_services() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph("parallel", "SOURCE", "SINK", (WorkSpec("N1", "calendar", "Create event"), WorkSpec("N2", "notes", "Find note")), (Edge("SOURCE", "N1"), Edge("SOURCE", "N2"), Edge("N1", "SINK"), Edge("N2", "SINK")))
    )
    scheduler = GraphScheduler(steward, ResourceTable())
    assignments = scheduler.schedule("parallel")
    assert {assignment.node_id for assignment in assignments} == {"N1", "N2"}


def test_single_service_capacity_queues_second_work_until_first_completes() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph("queue", "SOURCE", "SINK", (WorkSpec("N1", "notes", "Find first"), WorkSpec("N2", "notes", "Find second")), (Edge("SOURCE", "N1"), Edge("SOURCE", "N2"), Edge("N1", "SINK"), Edge("N2", "SINK")))
    )
    scheduler = GraphScheduler(steward, ResourceTable())
    first = scheduler.schedule("queue")
    assert len(first) == 1
    claim = scheduler.claim(first[0].assignment_id, "notes")
    steward.commit_node("queue", claim.node_id, claim.assignment_id)
    second = scheduler.assignments_for("notes")
    assert len(second) == 1
    assert second[0].node_id != claim.node_id


def test_topological_frontier_releases_join_only_after_all_predecessors_finish() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph(
            "join", "SOURCE", "SINK",
            (WorkSpec("N1", "calendar", "First branch"), WorkSpec("N2", "notes", "Second branch"), WorkSpec("N3", "payment", "Join work")),
            (Edge("SOURCE", "N1"), Edge("SOURCE", "N2"), Edge("N1", "N3"), Edge("N2", "N3"), Edge("N3", "SINK")),
        )
    )
    scheduler = GraphScheduler(steward, ResourceTable())
    frontier = scheduler.schedule("join")
    assert [assignment.node_id for assignment in frontier] == ["N1", "N2"]
    first = scheduler.claim(frontier[0].assignment_id, "calendar")
    steward.commit_node("join", first.node_id, first.assignment_id)
    assert steward.read_for_scheduler("join").node("N3").status is NodeStatus.BLOCKED
    second = scheduler.claim(frontier[1].assignment_id, "notes")
    steward.commit_node("join", second.node_id, second.assignment_id)
    assert steward.read_for_scheduler("join").node("N3").status is NodeStatus.ASSIGNED


def test_scheduler_leases_profile_default_resources() -> None:
    registry_with_display = RegistryTable(
        {"calendar": AppProfile("calendar", "Calendar", "Appointment app", ("create_event",), ("calendar.pkg",), default_resources=("foreground_display:primary",))}
    )
    steward = GraphSteward(registry_with_display)
    scheduler = GraphScheduler(steward, ResourceTable())
    steward.create_initial_graph(
        InitialGraph("resource", "SOURCE", "SINK", (WorkSpec("N1", "calendar", "Create event"),), (Edge("SOURCE", "N1"), Edge("N1", "SINK")))
    )
    assert scheduler.resources.snapshot()["foreground_display:primary"]["leased"] == 1


def test_critical_path_policy_prioritizes_longer_remaining_path_under_service_contention() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable(), CriticalPathPolicy())
    steward.create_initial_graph(
        InitialGraph(
            "critical",
            "SOURCE",
            "SINK",
            (
                WorkSpec("SHORT", "notes", "Short branch", metadata={"estimated_duration": 1}),
                WorkSpec("LONG", "notes", "Long branch start", metadata={"estimated_duration": 1}),
                WorkSpec("TAIL", "calendar", "Long branch tail", metadata={"estimated_duration": 10}),
            ),
            (Edge("SOURCE", "SHORT"), Edge("SOURCE", "LONG"), Edge("SHORT", "SINK"), Edge("LONG", "TAIL"), Edge("TAIL", "SINK")),
        )
    )
    assert scheduler.assignments_for("notes")[0].node_id == "LONG"


def test_fanout_policy_prioritizes_node_that_releases_more_work() -> None:
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable(), FanoutPolicy())
    steward.create_initial_graph(
        InitialGraph(
            "fanout",
            "SOURCE",
            "SINK",
            (
                WorkSpec("LEAF", "notes", "Leaf branch"),
                WorkSpec("FORK", "notes", "Shared prerequisite"),
                WorkSpec("C1", "calendar", "Consumer one"),
                WorkSpec("C2", "payment", "Consumer two"),
            ),
            (
                Edge("SOURCE", "LEAF"), Edge("SOURCE", "FORK"), Edge("LEAF", "SINK"),
                Edge("FORK", "C1"), Edge("FORK", "C2"), Edge("C1", "SINK"), Edge("C2", "SINK"),
            ),
        )
    )
    assert scheduler.assignments_for("notes")[0].node_id == "FORK"


def test_hybrid_policy_uses_explicit_duration_and_fanout_without_mutating_graph() -> None:
    steward = GraphSteward(registry())
    policy = HybridPolicy(critical_path_weight=1.0, fanout_weight=2.0, duration_weight=0.1)
    scheduler = GraphScheduler(steward, ResourceTable(), policy)
    steward.create_initial_graph(
        InitialGraph(
            "hybrid",
            "SOURCE",
            "SINK",
            (
                WorkSpec("A", "notes", "Independent", metadata={"estimated_duration": 1}),
                WorkSpec("B", "notes", "Unblock consumer", metadata={"estimated_duration": 2}),
                WorkSpec("C", "calendar", "Consumer", metadata={"estimated_duration": 2}),
            ),
            (Edge("SOURCE", "A"), Edge("SOURCE", "B"), Edge("A", "SINK"), Edge("B", "C"), Edge("C", "SINK")),
        )
    )
    before = steward.read_for_scheduler("hybrid").edges
    assert scheduler.assignments_for("notes")[0].node_id == "B"
    assert steward.read_for_scheduler("hybrid").edges == before


def test_global_frontier_compares_ready_nodes_from_multiple_task_graphs() -> None:
    key = ArtifactKey("shared_input", "fixture")
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable(), CriticalPathPolicy())
    steward.create_initial_graph(
        InitialGraph(
            "provider", "SOURCE", "SINK",
            (WorkSpec("P", "payment", "Produce shared input", ("shared_input",)),),
            (Edge("SOURCE", "P"), Edge("P", "SINK")),
        )
    )
    steward.declare_future(key, "provider", "P")
    steward.create_initial_graph(
        InitialGraph(
            "task-short", "SOURCE", "SINK",
            (WorkSpec("SHORT", "notes", "Short task", artifact_requirements=(key,), metadata={"estimated_duration": 1}),),
            (Edge("SOURCE", "SHORT"), Edge("SHORT", "SINK")),
        )
    )
    steward.create_initial_graph(
        InitialGraph(
            "task-long", "SOURCE", "SINK",
            (
                WorkSpec("LONG", "notes", "Long task start", artifact_requirements=(key,), metadata={"estimated_duration": 1}),
                WorkSpec("TAIL", "calendar", "Long task tail", metadata={"estimated_duration": 10}),
            ),
            (Edge("SOURCE", "LONG"), Edge("LONG", "TAIL"), Edge("TAIL", "SINK")),
        )
    )

    provider = scheduler.assignments_for("payment")[0]
    scheduler.claim(provider.assignment_id, "payment")
    steward.commit_node(
        "provider",
        "P",
        provider.assignment_id,
        (ArtifactDraft("shared_input", {"value": "ready", "evidence": ["fixture"]}, key=key),),
    )
    assignment = scheduler.assignments_for("notes")[0]
    assert (assignment.graph_id, assignment.node_id) == ("task-long", "LONG")
