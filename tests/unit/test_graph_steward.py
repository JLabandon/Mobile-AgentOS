import pytest

from mobile_agent_os.graph_space import ArtifactDraft, CheckpointExpansion, Edge, GraphSteward, InitialGraph, NodeStatus, WorkSpec
from mobile_agent_os.graph_space.steward import GraphError

from tests.fakes.runtime import registry


def _graph() -> InitialGraph:
    return InitialGraph(
        "g1",
        "SOURCE",
        "SINK",
        (WorkSpec("A", "calendar", "Start appointment"), WorkSpec("B", "calendar", "Finish appointment")),
        (Edge("SOURCE", "A"), Edge("A", "B"), Edge("B", "SINK")),
    )


def test_initial_graph_source_releases_first_work_and_sink_requires_evaluation() -> None:
    steward = GraphSteward(registry())
    snapshot = steward.create_initial_graph(_graph())
    assert snapshot.node("SOURCE").status is NodeStatus.DONE
    assert snapshot.node("A").status is NodeStatus.READY
    assert snapshot.node("B").status is NodeStatus.BLOCKED
    assert snapshot.node("SINK").status is NodeStatus.BLOCKED


def test_checkpoint_expansion_rewrites_a_to_b_as_a_and_d_to_c_to_b() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(_graph())
    steward.assign("g1", "A", "AS-A")
    steward.start("g1", "A", "AS-A")
    snapshot = steward.checkpoint_and_expand(
        "g1",
        CheckpointExpansion(
            origin_node_id="A",
            assignment_id="AS-A",
            checkpoint=ArtifactDraft("execution_checkpoint", {"title": "Alice meeting"}),
            provider=WorkSpec("D", "notes", "Find appointment location", ("information_result",)),
            continuation=WorkSpec("C", "calendar", "Continue appointment"),
            request_kind="information",
            required_capability="search_notes",
        ),
    )
    assert snapshot.node("A").status is NodeStatus.DONE
    assert snapshot.node("A").outcome == "checkpoint"
    assert snapshot.node("D").status is NodeStatus.READY
    assert snapshot.node("C").status is NodeStatus.BLOCKED
    assert snapshot.node("B").status is NodeStatus.BLOCKED
    assert {(edge.from_node_id, edge.to_node_id) for edge in snapshot.edges} >= {("A", "C"), ("D", "C"), ("C", "B")}
    assert ("A", "B") not in {(edge.from_node_id, edge.to_node_id) for edge in snapshot.edges}

    steward.assign("g1", "D", "AS-D")
    steward.start("g1", "D", "AS-D")
    snapshot = steward.commit_node(
        "g1",
        "D",
        "AS-D",
        (ArtifactDraft("information_result", {"value": "Googleplex", "evidence": ["Appointment note: Googleplex"]}),),
    )
    assert snapshot.node("C").status is NodeStatus.READY
    assert [artifact.kind for artifact in snapshot.input_artifacts("C")] == ["execution_checkpoint", "information_result"]


def test_scheduler_and_unrelated_nodes_cannot_read_artifact_payloads() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph(
            "isolation", "SOURCE", "SINK",
            (WorkSpec("A", "calendar", "Produce private result"), WorkSpec("B", "notes", "Independent work"), WorkSpec("C", "calendar", "Consume A")),
            (Edge("SOURCE", "A"), Edge("SOURCE", "B"), Edge("A", "C"), Edge("B", "SINK"), Edge("C", "SINK")),
        )
    )
    steward.assign("isolation", "A", "AS-A")
    steward.start("isolation", "A", "AS-A")
    steward.commit_node(
        "isolation",
        "A",
        "AS-A",
        (ArtifactDraft("private", {"value": "only C may read", "evidence": ["private source"], "secret": "only C may read"}),),
    )
    assert steward.read_for_scheduler("isolation").artifacts == ()
    assert steward.read_for_node("isolation", "B").artifacts == ()
    assert [artifact.payload for artifact in steward.read_for_node("isolation", "C").artifacts] == [{"value": "only C may read", "evidence": ["private source"], "secret": "only C may read"}]


def test_graph_rejects_cycles() -> None:
    steward = GraphSteward(registry())
    with pytest.raises(GraphError, match="acyclic"):
        steward.create_initial_graph(
            InitialGraph("cycle", "SOURCE", "SINK", (WorkSpec("A", "calendar", "A"), WorkSpec("B", "notes", "B")), (Edge("SOURCE", "A"), Edge("A", "B"), Edge("B", "A"), Edge("B", "SINK")))
        )


def test_failed_predecessor_keeps_successor_blocked_with_cause_and_exports_events(tmp_path) -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(_graph())
    steward.assign("g1", "A", "AS-A")
    steward.start("g1", "A", "AS-A")
    steward.fail_node("g1", "A", "AS-A", "fixture failure")
    snapshot = steward.read("g1")
    assert snapshot.node("B").status is NodeStatus.BLOCKED
    assert snapshot.node("B").metadata["blocked_by_failed_predecessors"] == ("A",)

    path = tmp_path / "events.jsonl"
    steward.write_events_jsonl("g1", str(path))
    assert "node_failed" in path.read_text(encoding="utf-8")


def test_completion_gate_rejects_empty_required_artifact_without_publishing_it() -> None:
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph(
            "contract", "SOURCE", "SINK",
            (WorkSpec("A", "notes", "Retrieve information", ("information_result",)),),
            (Edge("SOURCE", "A"), Edge("A", "SINK")),
        )
    )
    steward.assign("contract", "A", "AS-A")
    steward.start("contract", "A", "AS-A")
    snapshot = steward.commit_node("contract", "A", "AS-A", (ArtifactDraft("information_result", {}),))
    assert snapshot.node("A").status is NodeStatus.FAILED
    assert snapshot.artifacts == ()
    assert "has no result value" in (snapshot.node("A").outcome or "")
    assert steward.events("contract")[-1].kind == "completion_rejected"
