from mobile_agent_os.graph_space import ArtifactDraft, ArtifactKey, ArtifactState, Edge, GraphSteward, InitialGraph, NodeStatus, WorkSpec

from tests.fakes.runtime import registry


def _consumer_graph(graph_id: str, node_id: str, key: ArtifactKey) -> InitialGraph:
    return InitialGraph(
        graph_id,
        "SOURCE",
        "SINK",
        (WorkSpec(node_id, "calendar", "Use shared information", artifact_requirements=(key,)),),
        (Edge("SOURCE", node_id), Edge(node_id, "SINK")),
    )


def test_exact_future_is_shared_by_two_task_graphs_and_publishes_once() -> None:
    key = ArtifactKey("weather_forecast", "Shenzhen", valid_time="2026-08-24", source_app="notes")
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph(
            "provider",
            "SOURCE",
            "SINK",
            (WorkSpec("P", "notes", "Retrieve weather", ("weather_forecast",)),),
            (Edge("SOURCE", "P"), Edge("P", "SINK")),
        )
    )
    artifact, created = steward.declare_future(key, "provider", "P")
    assert created
    assert artifact.state is ArtifactState.FUTURE

    steward.create_initial_graph(_consumer_graph("task-a", "A", key))
    steward.create_initial_graph(_consumer_graph("task-b", "B", key))
    assert steward.read("task-a").node("A").status is NodeStatus.BLOCKED
    assert steward.read("task-b").node("B").status is NodeStatus.BLOCKED
    assert set(steward.shared_artifact(key).consumers) == {("task-a", "A"), ("task-b", "B")}

    steward.assign("provider", "P", "AS-P")
    steward.start("provider", "P", "AS-P")
    steward.commit_node(
        "provider",
        "P",
        "AS-P",
        (
            ArtifactDraft(
                "weather_forecast",
                {"value": "sunny", "evidence": ["forecast note"]},
                ("fixture://weather",),
                key,
            ),
        ),
    )
    assert steward.shared_artifact(key).state is ArtifactState.CONCRETE
    assert steward.read("task-a").node("A").status is NodeStatus.READY
    assert steward.read("task-b").node("B").status is NodeStatus.READY
    assert steward.read_for_node("task-a", "A").input_artifacts("A")[0].payload["value"] == "sunny"


def test_concrete_artifact_serves_late_task_and_invalidation_blocks_reuse() -> None:
    key = ArtifactKey("project_code", "Project Alpha", version="v1", source_app="notes")
    steward = GraphSteward(registry())
    steward.create_initial_graph(
        InitialGraph(
            "provider",
            "SOURCE",
            "SINK",
            (WorkSpec("P", "notes", "Retrieve project code", ("project_code",)),),
            (Edge("SOURCE", "P"), Edge("P", "SINK")),
        )
    )
    steward.declare_future(key, "provider", "P")
    steward.assign("provider", "P", "AS-P")
    steward.start("provider", "P", "AS-P")
    steward.commit_node(
        "provider",
        "P",
        "AS-P",
        (ArtifactDraft("project_code", {"value": "ALPHA-42", "evidence": ["project note"]}, key=key),),
    )

    steward.create_initial_graph(_consumer_graph("late-task", "L", key))
    assert steward.read("late-task").node("L").status is NodeStatus.READY

    steward.invalidate_shared_artifact(key, "source version changed")
    assert steward.read("late-task").node("L").status is NodeStatus.BLOCKED
    steward.create_initial_graph(_consumer_graph("new-task", "N", key))
    assert steward.read("new-task").node("N").status is NodeStatus.BLOCKED
