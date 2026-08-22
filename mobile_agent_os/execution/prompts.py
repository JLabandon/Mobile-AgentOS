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
        "artifact": {"type": "object"},
        "message": {"type": "string"},
        "required_capability": {"type": "string"},
        "need": {"type": "string"},
        "target_agent": {"type": "string"},
        "provider_goal": {"type": "string"},
    },
    "required": ["action"],
}


COMPLETION_REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "artifact_kind": {"type": "string", "minLength": 1},
        "artifact": {
            "type": "object",
            "properties": {
                "value": {},
                "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["value", "evidence"],
        },
    },
    "required": ["artifact_kind", "artifact"],
}


def build_action_prompt(*, width: int, height: int, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> str:
    return (
        "You are an AppAgent executing one assigned mobile work unit. Return one JSON object only. "
        "The top-level field action is required; do not wrap an action inside another object. "
        "Use only primitive actions: click, input_text, swipe, back. "
        "For click or input_text, use element_id from the visible UI list when available; otherwise include integer x and y. "
        "input_text also requires text. Use the assigned work, execution history, structured visible UI list, and screenshot together to choose the next action. "
        "The structured visible UI list provides the current control and value state; the screenshot provides visual layout. Return complete when you judge that the available evidence fulfills the assigned work. "
        "complete must return artifact_kind and an artifact object with a non-empty value and evidence. "
        "value is the result for downstream work; evidence is visible text or facts supporting that result. Prefer the assigned work's expected artifact type when one is shown in context. "
        "When a required input is unavailable, return request_information or request_operation with fields required_capability, need, and optional target_agent. "
        "Do not invent UI state or information. "
        f"Screenshot size: {width}x{height}. Agent: {agent_name}. App: {app_label}. "
        f"Assigned work: {task_instruction}. Context: {memory or '<none>'}"
    )


def build_information_response_prompt(**kwargs: object) -> str:
    return build_action_prompt(**kwargs)  # Information work uses the same AppAgent protocol.


def build_completion_report_prompt(*, agent_name: str, app_label: str, task_instruction: str, artifact_kind: str, memory: str = "") -> str:
    return (
        "You are producing a completion report for one finished mobile work unit. Return one JSON object only. "
        "Extract the result required by the work from the current visible UI. artifact_kind must be the requested type. "
        "artifact.value contains the result for downstream work. artifact.evidence is a non-empty list of visible text or facts supporting it. "
        "Do not infer facts that are not visible. "
        f"Agent: {agent_name}. App: {app_label}. Assigned work: {task_instruction}. Required artifact kind: {artifact_kind}. Context: {memory or '<none>'}"
    )
