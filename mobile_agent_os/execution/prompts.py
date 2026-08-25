from __future__ import annotations


ACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["click", "input_text", "swipe", "back", "complete", "fail", "request_information", "request_operation"],
        },
        "element_id": {"type": "integer"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "text": {"type": "string"},
        "direction": {"type": "string"},
        "artifact_kind": {"type": "string"},
        "artifact_node_id": {"type": "string"},
        "artifact": {"type": "object"},
        "message": {"type": "string"},
        "required_capability": {"type": "string"},
        "need": {"type": "string"},
        "target_agent": {"type": "string"},
        "artifact_identity": {
            "type": "object",
            "properties": {
                "schema_id": {"type": "string"},
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["name", "value"],
                    },
                },
            },
            "required": ["schema_id", "parameters"],
        },
    },
    "required": ["action"],
}


def build_action_prompt(*, width: int, height: int, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> str:
    return (
        "You are an AppAgent executing one assigned mobile work unit. Return one JSON object only. "
        "The top-level field action is required; do not wrap an action inside another object. "
        "Use only primitive actions: click, input_text, swipe, back. "
        "For click or input_text, use element_id from the visible UI list when available; otherwise include integer x and y. "
        "input_text also requires text. Use the assigned work, execution history, structured visible UI list, and screenshot together to choose the next action. "
        "The structured visible UI list provides the current control and value state; the screenshot provides visual layout. Return complete when you judge that the available evidence fulfills the assigned work. "
        "complete must return artifact_kind and an artifact object with a non-empty value and evidence. Include artifact_node_id when multiple expected outputs share one kind. "
        "value is the result for downstream work; evidence is visible text or facts supporting that result. Prefer the assigned work's expected artifact type when one is shown in context. "
        "When a required input is unavailable, return request_information or request_operation with required_capability and need. "
        "target_agent may be omitted only when the Registry lists exactly one provider for that capability. "
        "When a matching Artifact schema and all identity fields are known, include artifact_identity with schema_id and name/value parameter pairs. "
        "Do not invent UI state or information. "
        f"Screenshot size: {width}x{height}. Agent: {agent_name}. App: {app_label}. "
        f"Assigned work: {task_instruction}. Context: {memory or '<none>'}"
    )
