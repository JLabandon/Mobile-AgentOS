from __future__ import annotations

import json
from typing import Any

from .runtime_requests import RuntimeInformationRequest, RuntimeOperationRequest


def app_system_prompt() -> str:
    return (
        "You are an app-oriented mobile AppAgent. Return json only. "
        "You control exactly one mobile app. Use your app profile, task guideline memory, current UI, "
        "session memory, and IPC messages to decide the next step. "
        "You may choose exactly one action from click, input, swipe, back, REQUEST_INFORMATION, RESPOND_INFORMATION, REQUEST_OPERATION, RESPOND_OPERATION, FINISH. "
        "Never include private reasoning or a long explanation inside JSON fields; keep reason under 20 words. "
        "If you cannot continue because another app agent has needed information, choose REQUEST_INFORMATION. "
        "If you cannot continue because another app agent must complete an action, choose REQUEST_OPERATION. "
        "If your assigned task is to answer a runtime request and the current app UI supports an answer, choose RESPOND_INFORMATION. "
        "RESPOND_INFORMATION and RESPOND_OPERATION are valid only when an Incoming RuntimeInformationRequest or Incoming RuntimeOperationRequest is present. "
        "For a normal top-level provider task, navigate until the requested evidence is visible, then FINISH; "
        "the runtime will deliver visible evidence to dependent peer agents. "
        "Use only the UI nodes and memories given by the user. Complete the requested flow. "
        "If a text field or search box is visible and you need to type, fill, edit, or search, choose input directly with the intended text; do not click merely to focus the field. "
        "Placeholder controls such as Add description, Add note, Add details, or Add location can be input targets when you need to enter text into them. "
        "You may tap Save, Done, OK, Create, or final confirmation controls. "
        "Return FINISH only after the final record is visible or the requested alarm/event is already present. "
        "JSON schema for UI actions: {\"action\":\"click|input|swipe|back|FINISH\","
        "\"target_id\":0,\"target_text\":\"optional\",\"text\":\"optional\","
        "\"direction\":\"up|down|left|right\",\"reason\":\"short\"}. "
        "JSON schema for information requests: {\"action\":\"REQUEST_INFORMATION\","
        "\"to_agent\":\"target_agent\",\"need\":\"needed information\",\"context\":\"task context\","
        "\"purpose\":\"why it is needed\",\"resume_instruction\":\"how to continue after response\","
        "\"reason\":\"short\"}. "
        "JSON schema for information responses: {\"action\":\"RESPOND_INFORMATION\","
        "\"status\":\"success|failed\",\"information\":\"short answer or empty on failure\","
        "\"evidence\":\"visible text or memory supporting the answer\","
        "\"confidence\":\"high|medium|low\",\"limitations\":\"optional\",\"reason\":\"short\"}. "
        "JSON schema for operation requests: {\"action\":\"REQUEST_OPERATION\","
        "\"to_agent\":\"target_agent\",\"operation\":\"operation to perform\",\"context\":\"task context\","
        "\"purpose\":\"why it is needed\",\"expected_result\":\"what success means\","
        "\"resume_instruction\":\"how to continue after response\",\"reason\":\"short\"}. "
        "JSON schema for operation responses: {\"action\":\"RESPOND_OPERATION\","
        "\"status\":\"success|failed\",\"result\":\"operation result\","
        "\"evidence\":\"visible text supporting result\",\"limitations\":\"optional\",\"reason\":\"short\"}. "
        "Example json output: {\"action\":\"FINISH\",\"reason\":\"requested item is visible\"}."
    )


def app_profile_prompt(agent: Any) -> str:
    peer_text = "\n".join(
        f"- {peer.name}_agent: {peer.label}; capabilities: {', '.join(peer.capabilities) or 'none'}; description: {peer.description}"
        for peer in agent.available_peers
    )
    return (
        f"App profile:\n"
        f"- agent_name: {agent.name}\n"
        f"- app_label: {agent.config.label}\n"
        f"- app_description: {agent.config.description or 'No description provided.'}\n"
        f"- capabilities: {', '.join(agent.config.capabilities) or 'none'}\n"
        "Available IPC peers:\n"
        f"{peer_text or '- none'}\n"
        "Task guideline memory:\n"
        + "\n".join(f"- {item}" for item in agent.config.task_guidelines)
        + ("\n" if agent.config.task_guidelines else "- none\n")
        + "Long-term app memory:\n"
        + "\n".join(f"- {item}" for item in agent.long_term_memory[-8:])
        + ("\n" if agent.long_term_memory else "- none\n")
    )


def app_user_prompt(
    agent: Any,
    *,
    subtask: Any,
    step: int,
    ui_text: str,
    term_status_text: str = "",
    incoming_request: RuntimeInformationRequest | RuntimeOperationRequest | None = None,
    blocked_action_text: str = "",
) -> str:
    info_text = ""
    if agent.received_information:
        info_text = "Received runtime information:\n" + "\n".join(
            f"- {response.information} (from {response.from_agent}; evidence: {response.evidence})" for response in agent.received_information
        ) + "\n"
        info_text += (
            "Use the received information according to the current app UI and assigned task. "
            "If the task requires the received information to appear in a final record, make it visible before Save/Done/OK/Create or FINISH.\n"
            "Do not request the same information again; continue with the information already received.\n"
            "Choose the appropriate UI field, slot, or candidate yourself according to the current UI, app profile, and assigned task. "
            "The executor will only perform primitive click/input/swipe/back actions; it will not choose app-specific fields for you.\n"
        )
    operation_text = ""
    if agent.received_operations:
        operation_text = "Received runtime operation results:\n" + "\n".join(
            f"- {response.result} (from {response.from_agent}; status: {response.status}; evidence: {response.evidence})"
            for response in agent.received_operations
        ) + "\nContinue according to the operation result; do not request the same operation again.\n"
    required_text = ""
    if subtask.required_terms:
        required_text = "Required terms that must be visible before final save/FINISH: " + ", ".join(subtask.required_terms) + "\n"
    forbidden_text = ""
    if subtask.forbidden_terms:
        forbidden_text = "Terms that must not still be visible at FINISH: " + ", ".join(subtask.forbidden_terms) + "\n"
    request_text = ""
    if isinstance(incoming_request, RuntimeInformationRequest):
        request_text = (
            "Incoming RuntimeInformationRequest:\n"
            f"{json.dumps(incoming_request, default=lambda obj: obj.__dict__, ensure_ascii=False, indent=2)}\n"
            "Your current assigned task is to use your own app and memory to answer this request if possible. "
            "Do not decide the requesting agent's UI actions.\n"
        )
    elif isinstance(incoming_request, RuntimeOperationRequest):
        request_text = (
            "Incoming RuntimeOperationRequest:\n"
            f"{json.dumps(incoming_request, default=lambda obj: obj.__dict__, ensure_ascii=False, indent=2)}\n"
            "Your current assigned task is to perform the requested operation inside your own app if possible. "
            "If a visible UI action is required, perform it first; return RESPOND_OPERATION only after the result is visible or clearly unavailable. "
            "If the visible UI already shows the requested operation result, return RESPOND_OPERATION now and do not repeat the same click.\n"
        )
    memory_text = ""
    if agent.session_memory:
        memory_text = "Session working memory:\n" + "\n".join(f"- {item}" for item in agent.session_memory[-8:]) + "\n"
    return (
        f"{app_profile_prompt(agent)}"
        f"Instruction: {subtask.instruction}\n"
        f"Step: {step}\n"
        f"{request_text}"
        f"{memory_text}"
        f"{info_text}"
        f"{operation_text}"
        f"{required_text}"
        f"{forbidden_text}"
        f"{term_status_text}"
        f"{blocked_action_text}"
        "Important: complete the requested event/alarm. If a Save/Done/OK/Create button is needed and visible, tap it. "
        "If some required information is already visible, preserve it and focus on missing information. "
        "If you need to fill a visible text field, choose input directly instead of click. "
        "If a visible field already contains the desired value, do not edit that field again; move to another missing field or confirm the dialog/form. "
        "If typing creates visible candidate rows or confirmation choices, inspect the next UI and choose the next primitive action yourself. "
        "If the required values are already visible and a final confirmation control such as OK, Save, Done, Create, or Set is visible, choose that confirmation control instead of reselecting the same value. "
        "In sub-dialogs such as time pickers, search pickers, and candidate selectors, OK/Done may simply confirm the sub-dialog and return to the main form; use it when the sub-dialog values are correct, even if other task fields remain unfinished. "
        "If the current viewport is about an already-filled field, do not keep reopening it; go back or scroll to find a different useful control. "
        "Return FINISH only after completion.\n"
        "Visible UI nodes:\n"
        f"{ui_text}\n"
        "Return one json action object matching the schema exactly."
    )
