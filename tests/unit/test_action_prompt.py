from mobile_agent_os.execution.prompts import ACTION_JSON_SCHEMA, COMPLETION_REPORT_JSON_SCHEMA, build_action_prompt


def test_action_prompt_names_runtime_request_fields() -> None:
    prompt = build_action_prompt(
        width=100,
        height=200,
        agent_name="appointment_desk",
        app_label="Appointment Desk",
        task_instruction="Complete the assigned work.",
    )

    assert "required_capability" in prompt
    assert "target_agent" in prompt
    assert "element_id" in prompt
    assert "artifact_kind" in prompt
    assert "top-level field action" in prompt


def test_action_schema_requires_one_top_level_action() -> None:
    assert ACTION_JSON_SCHEMA["required"] == ["action"]
    assert "request_information" in ACTION_JSON_SCHEMA["properties"]["action"]["enum"]
    assert COMPLETION_REPORT_JSON_SCHEMA["properties"]["artifact"]["required"] == ["value", "evidence"]
