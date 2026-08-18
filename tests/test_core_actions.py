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


def test_vlm_parser_normalizes_back_object() -> None:
    assert _parse_json_object('{"back":""}') == {"action": "back"}
