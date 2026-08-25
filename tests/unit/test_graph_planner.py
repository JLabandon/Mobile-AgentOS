from mobile_agent_os.planner import GraphPlanner
from mobile_agent_os.planner.graph_planner import PLANNER_JSON_SCHEMA

from tests.fakes.runtime import FakeTextClient, registry


def test_planner_builds_work_artifact_fragment_without_task_routes() -> None:
    client = FakeTextClient(
        {
            "work": [
                {"node_id": "fetch", "agent_id": "notes", "goal": "Find meeting details"},
                {"node_id": "create", "agent_id": "calendar", "goal": "Create meeting"},
            ],
            "control_edges": [],
            "artifacts": [
                {
                    "local_id": "details",
                    "kind": "appointment_details",
                    "producer_work_id": "fetch",
                    "consumer_work_ids": ["create"],
                    "identity": {
                        "schema_id": "appointment.location",
                        "parameters": [
                            {"name": "participant", "value": "Alice"},
                            {"name": "start_time", "value": "2026-08-26T15:00:00+08:00"},
                        ],
                    },
                }
            ],
            "terminal_work_ids": ["create"],
        }
    )
    fragment = GraphPlanner(registry(), client).plan("planned", "Schedule Alice's meeting at the specified time.")
    assert fragment.task_id == "planned"
    assert fragment.artifacts[0].producer_work_id == "fetch"
    assert fragment.artifacts[0].consumer_work_ids == ("create",)
    assert fragment.artifacts[0].identity.schema_id == "appointment.location"
    assert "long_term_memory" not in client.last_user
    assert "benchmark" not in client.last_user.lower()
    assert client.last_json_schema == PLANNER_JSON_SCHEMA


def test_planner_can_leave_runtime_information_out_of_initial_fragment() -> None:
    client = FakeTextClient(
        {
            "work": [{"node_id": "appointment", "agent_id": "calendar", "goal": "Complete the appointment form"}],
            "control_edges": [],
            "artifacts": [],
            "terminal_work_ids": ["appointment"],
        }
    )
    fragment = GraphPlanner(registry(), client).plan("late", "Complete the appointment record.")
    assert fragment.artifacts == ()
    assert fragment.terminal_work_ids == ("appointment",)
