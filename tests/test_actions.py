import pytest

from agent_ipc_mvp.actions import ActionError, AgentAction


def test_valid_click_action() -> None:
    action = AgentAction.from_json({"action": "click", "target_id": 3, "reason": "open"})
    assert action.action == "click"
    assert action.target_id == 3


def test_valid_input_without_explicit_target_uses_editable_fallback() -> None:
    action = AgentAction.from_json({"action": "input", "text": "hello"})
    assert action.text == "hello"


def test_reject_unknown_action() -> None:
    with pytest.raises(ActionError):
        AgentAction.from_json({"action": "save"})
