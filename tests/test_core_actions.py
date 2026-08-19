import pytest

from mobile_agent_os.app_agents.actions import ActionError, AgentAction
from mobile_agent_os.model_clients.deepseek import DeepSeekClient
from mobile_agent_os.model_clients.gemini import _parse_json_object


def test_request_information_action_parses() -> None:
    action = AgentAction.from_json(
        {
            "action": "REQUEST_INFORMATION",
            "to_agent": "keep_agent",
            "need": "meeting details",
            "context": "calendar event",
            "purpose": "finish event",
            "resume_instruction": "use the answer in Calendar",
        }
    )
    assert action.to_agent == "keep_agent"
    assert action.need == "meeting details"


def test_response_information_requires_status() -> None:
    with pytest.raises(ActionError):
        AgentAction.from_json({"action": "RESPOND_INFORMATION", "information": "Googleplex"})


def test_llm_json_parser_extracts_object_from_extra_text() -> None:
    parsed = DeepSeekClient.__new__(DeepSeekClient).parse_json_content(
        '{"action":"input","text":"Googleplex"}\n\nextra model text {"ignored": true}'
    )
    assert parsed == {"action": "input", "text": "Googleplex"}


def test_vlm_parser_normalizes_point_click() -> None:
    assert _parse_json_object('{"type":"click","point":[78,49]}') == {"action": "click", "x": 78, "y": 49}


def test_vlm_parser_normalizes_click_object() -> None:
    assert _parse_json_object('{"click":{"x":360,"y":432}}') == {"action": "click", "x": 360, "y": 432}


def test_vlm_parser_normalizes_complete_object() -> None:
    assert _parse_json_object('{"complete":{"message":"done"}}') == {"action": "complete", "message": "done"}


def test_vlm_parser_normalizes_input_text_object() -> None:
    assert _parse_json_object('{"input_text":{"text":"Googleplex"}}') == {"action": "input_text", "text": "Googleplex"}


def test_vlm_parser_normalizes_back_object() -> None:
    assert _parse_json_object('{"back":""}') == {"action": "back"}


def test_vlm_parser_normalizes_click_area_object() -> None:
    assert _parse_json_object('{"click_area":{"x1":55,"y1":550,"x2":665,"y2":660}}') == {
        "action": "click_area",
        "x1": 55,
        "y1": 550,
        "x2": 665,
        "y2": 660,
    }


def test_vlm_parser_normalizes_click_area_list() -> None:
    assert _parse_json_object('{"action":"click_area","area":[55,550,665,660]}') == {
        "action": "click_area",
        "x1": 55,
        "y1": 550,
        "x2": 665,
        "y2": 660,
    }


def test_planner_edges_accept_typed_information_edge() -> None:
    from mobile_agent_os.benchmark.run_job_level_demo import _planner_edges, _planned_dependencies

    planned = {
        "edges": [
            {
                "edge_id": "e1",
                "from_run_id": "run_keep",
                "to_run_id": "run_calendar",
                "type": "information",
                "artifact": "appointment location",
            }
        ]
    }
    edges = _planner_edges(planned)
    assert edges[0]["type"] == "information"
    assert _planned_dependencies(planned)[0]["kind"] == "information"


def test_planner_edges_preserve_legacy_dependencies() -> None:
    from mobile_agent_os.benchmark.run_job_level_demo import _planner_edges

    planned = {
        "dependencies": [
            {
                "dependency_id": "dep1",
                "producer_run_id": "run_payment",
                "consumer_run_id": "run_shop",
                "kind": "operation",
                "artifact": "payment approval",
            }
        ]
    }
    assert _planner_edges(planned) == [
        {
            "edge_id": "dep1",
            "from_run_id": "run_payment",
            "to_run_id": "run_shop",
            "type": "operation",
            "artifact": "payment approval",
            "required": "true",
            "rationale": "legacy dependency",
        }
    ]


def test_planner_contract_rejects_status_only_information_edge_from_non_provider() -> None:
    from mobile_agent_os.android.vlm_ui import DemoAgent
    from mobile_agent_os.benchmark.run_job_level_demo import _planner_contract_issues

    agents = {
        "task_a_agent": DemoAgent(name="task_a_agent", app_label="Task A", package="a", display_id=0, capabilities=("complete_form",)),
        "task_b_agent": DemoAgent(name="task_b_agent", app_label="Task B", package="b", display_id=1, capabilities=("complete_form",)),
    }
    issues = _planner_contract_issues(
        {
            "runs": [
                {"run_id": "run_a", "agent_id": "task_a_agent", "instruction": "Complete A"},
                {"run_id": "run_b", "agent_id": "task_b_agent", "instruction": "Complete B"},
            ],
            "edges": [
                {
                    "edge_id": "bad",
                    "from_run_id": "run_a",
                    "to_run_id": "run_b",
                    "type": "information",
                    "artifact": "completion status of Task A",
                }
            ],
        },
        agents,
    )
    assert issues
