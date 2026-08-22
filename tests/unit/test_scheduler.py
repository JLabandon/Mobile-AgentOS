from mobile_agent_os.graph_space import AppProfile, Edge, GraphSteward, InitialGraph, NodeStatus, RegistryTable, WorkSpec
from mobile_agent_os.scheduling import FifoScheduler, ResourceTable

from tests.fakes.runtime import registry


def test_fifo_scheduler_assigns_independent_work_with_independent_services() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph("parallel", "SOURCE", "SINK", (WorkSpec("N1", "calendar", "Create event"), WorkSpec("N2", "notes", "Find note")), (Edge("SOURCE", "N1"), Edge("SOURCE", "N2"), Edge("N1", "SINK"), Edge("N2", "SINK")))
    )
    scheduler = FifoScheduler(steward, ResourceTable())
    assignments = scheduler.schedule("parallel")
    assert {assignment.node_id for assignment in assignments} == {"N1", "N2"}


def test_single_service_capacity_queues_second_work_until_first_completes() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph("queue", "SOURCE", "SINK", (WorkSpec("N1", "notes", "Find first"), WorkSpec("N2", "notes", "Find second")), (Edge("SOURCE", "N1"), Edge("SOURCE", "N2"), Edge("N1", "SINK"), Edge("N2", "SINK")))
    )
    scheduler = FifoScheduler(steward, ResourceTable())
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
    scheduler = FifoScheduler(steward, ResourceTable())
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
    scheduler = FifoScheduler(steward, ResourceTable())
    steward.create_initial_graph(
        InitialGraph("resource", "SOURCE", "SINK", (WorkSpec("N1", "calendar", "Create event"),), (Edge("SOURCE", "N1"), Edge("N1", "SINK")))
    )
    assert scheduler.resources.snapshot()["foreground_display:primary"]["leased"] == 1
