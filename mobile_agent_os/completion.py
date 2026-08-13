from __future__ import annotations

import re
from typing import Any

from .runtime_requests import RuntimeInformationResponse, RuntimeOperationResponse
from .ui_tree import visible_texts


def normalized_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def salient_received_information_terms(responses: list[RuntimeInformationResponse]) -> list[str]:
    terms: list[str] = []
    negative_markers = (
        "not found",
        "not specified",
        "not available",
        "unavailable",
        "unknown",
        "missing",
        "does not contain",
        "no ",
        "none",
        "n/a",
    )
    for response in responses:
        if response.status != "success":
            continue
        for chunk in (response.information or "").replace("\n", ";").split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            normalized_chunk = chunk.lower()
            if any(marker in normalized_chunk for marker in negative_markers):
                continue
            if normalized_chunk.startswith(("yes,", "yes ", "no,", "no ")):
                continue
            key, sep, tail = chunk.partition(":")
            normalized_key = re.sub(r"[^A-Za-z]", "", key)
            if normalized_key.lower() in {"date", "receiveddate", "emaildate", "sentdate"}:
                continue
            if normalized_key.lower() in {"address", "placeaddress", "agenda", "note", "notes", "description", "details"}:
                continue
            value = tail if sep and normalized_key.isalpha() else chunk
            value = value.strip(" .,'\"")
            nested_key, nested_sep, nested_tail = value.partition(":")
            normalized_nested_key = re.sub(r"[^A-Za-z]", "", nested_key)
            if nested_sep and normalized_nested_key.isalpha():
                value = nested_tail.strip(" .,'\"")
            for marker in [". No ", ". no ", ". Not ", ". not "]:
                if marker in value:
                    value = value.split(marker, 1)[0].strip(" .,'\"")
            if not value:
                continue
            normalized_value = value.lower()
            if normalized_value in {"not specified", "none", "unknown", "n/a", "unavailable"}:
                continue
            if any(marker in normalized_value for marker in negative_markers):
                continue
            if len(value) < 3:
                continue
            if value not in terms:
                terms.append(value)
    return terms[:8]


def requires_final_commit(instruction: str) -> bool:
    markers = (
        "save",
        "create",
        "complete",
        "schedule",
        "set an alarm",
        "create an alarm",
        "authorize",
        "submit",
        "finish the order",
    )
    return any(marker in instruction.lower() for marker in markers)


def visible_final_confirmation_controls(instruction: str, nodes: list[Any]) -> list[str]:
    if not requires_final_commit(instruction):
        return []
    final_labels = {"save", "done", "ok", "create", "submit", "confirm", "authorize", "pay", "place order", "set alarm"}
    visible: list[str] = []
    for node in nodes:
        label = (node.text or node.content_desc or "").strip()
        if not label:
            continue
        normalized = label.lower()
        if node.enabled and (node.clickable or node.content_desc) and normalized in final_labels and label not in visible:
            visible.append(label)
    return visible


def is_final_confirmation_action(action: Any, nodes: list[Any]) -> bool:
    if getattr(action, "action", None) != "click":
        return False
    target_id = getattr(action, "target_id", None)
    target_text = getattr(action, "target_text", None)
    final_labels = {"save", "done", "ok", "create", "submit", "confirm", "authorize", "pay", "place order", "set alarm"}
    for node in nodes:
        if target_id is not None and getattr(node, "index", None) != target_id:
            continue
        if target_id is None and target_text:
            label_text = " ".join(
                [
                    getattr(node, "text", ""),
                    getattr(node, "content_desc", ""),
                    getattr(node, "resource_id", ""),
                ]
            ).lower()
            if target_text.lower() not in label_text:
                continue
        if target_id is None and not target_text:
            continue
        label = (getattr(node, "text", "") or getattr(node, "content_desc", "") or "").strip().lower()
        return label in final_labels
    return False


def requires_runtime_response(instruction: str) -> bool:
    markers = (
        "request_information",
        "request_operation",
        "runtime information",
        "runtime operation",
        "peer",
        "before saving, request",
        "before saving request",
    )
    return any(marker in instruction.lower() for marker in markers)


def verify_completion(
    *,
    instruction: str,
    required_terms: tuple[str, ...],
    forbidden_terms: tuple[str, ...],
    nodes: list[Any],
    received_information: list[RuntimeInformationResponse],
    received_operations: list[RuntimeOperationResponse],
    foreground_package: str,
    expected_package: str | None,
) -> tuple[bool, str]:
    texts = visible_texts(nodes, limit=120)
    operation_texts = [response.result for response in received_operations]
    haystack = "\n".join([*texts, *operation_texts]).lower()
    normalized_haystack = normalized_match_text("\n".join([*texts, *operation_texts]))
    visible_required = [term for term in required_terms if normalized_match_text(term) in normalized_haystack]
    missing = [term for term in required_terms if normalized_match_text(term) not in normalized_haystack]
    present_forbidden = [term for term in forbidden_terms if term.strip().lower() in haystack]
    pending_confirmation = visible_final_confirmation_controls(instruction, nodes)
    wrong_foreground = foreground_package != expected_package
    if requires_runtime_response(instruction) and not received_information and not received_operations:
        parts = ["runtime response required before completion"]
        if wrong_foreground:
            parts.append(f"wrong foreground package: expected={expected_package}, foreground={foreground_package}")
        return False, "; ".join(parts)
    if missing or present_forbidden or pending_confirmation:
        parts = []
        if visible_required:
            parts.append(f"visible required terms: {visible_required}")
        if missing:
            parts.append(f"missing required terms: {missing}")
        if present_forbidden:
            parts.append(f"forbidden terms still visible: {present_forbidden}")
        if pending_confirmation:
            parts.append(f"final confirmation controls still visible: {pending_confirmation}")
        if wrong_foreground:
            parts.append(f"wrong foreground package: expected={expected_package}, foreground={foreground_package}")
        return False, "; ".join(parts)
    if wrong_foreground:
        return False, f"wrong foreground package: expected={expected_package}, foreground={foreground_package}"
    return True, "completion terms verified"


def term_status_text(
    *,
    required_terms: tuple[str, ...],
    nodes: list[Any],
    received_information: list[RuntimeInformationResponse],
) -> str:
    if not required_terms:
        return ""
    texts = visible_texts(nodes, limit=120)
    normalized_haystack = normalized_match_text("\n".join(texts))
    visible = [term for term in required_terms if normalized_match_text(term) in normalized_haystack]
    not_visible = [term for term in required_terms if normalized_match_text(term) not in normalized_haystack]
    lines = ["Current visible required-term status:"]
    lines.append(f"- visible now: {', '.join(visible) if visible else 'none'}")
    lines.append(f"- not visible yet: {', '.join(not_visible) if not_visible else 'none'}")
    if received_information:
        received_text = " ".join(response.information for response in received_information).lower()
        normalized_received = normalized_match_text(received_text)
        received_not_visible = [
            term for term in required_terms
            if normalized_match_text(term) in normalized_received and normalized_match_text(term) not in normalized_haystack
        ]
        if received_not_visible:
            lines.append("- received from IPC but not visible in this app yet: " + ", ".join(received_not_visible))
        salient_not_visible = [
            term for term in salient_received_information_terms(received_information)
            if normalized_match_text(term) not in normalized_haystack
        ]
        if salient_not_visible:
            lines.append("- salient IPC information not visible in this app yet: " + ", ".join(salient_not_visible))
    return "\n".join(lines) + "\n"
