from mobile_agent_os.graph_space import NodeKind
from mobile_agent_os.planner import GraphPlanner
from mobile_agent_os.planner.graph_planner import PLANNER_JSON_SCHEMA

from tests.fakes.runtime import FakeTextClient, registry


def test_planner_adds_source_and_sink_without_task_specific_routes() -> None:
    client = FakeTextClient(
        {
            "work": [
                {"node_id": "N1", "agent_id": "notes", "goal": "Find meeting details", "expected_artifact_kinds": ["meeting_details"]},
                {"node_id": "N2", "agent_id": "calendar", "goal": "Create meeting"},
            ],
            "edges": [{"from_node_id": "N1", "to_node_id": "N2", "artifact_kinds": ["meeting_details"]}],
        }
    )
    plan = GraphPlanner(registry(), client).plan("planned", "Schedule Alice's meeting.")
    assert plan.source_id == "SOURCE"
    assert plan.sink_id == "SINK"
    assert {(edge.from_node_id, edge.to_node_id) for edge in plan.edges} >= {("SOURCE", "N1"), ("N1", "N2"), ("N2", "SINK")}
    assert "long_term_memory" not in client.last_user
    assert "benchmark" not in client.last_user.lower()
    assert "shared domain" in client.last_system
    assert client.last_json_schema == PLANNER_JSON_SCHEMA
