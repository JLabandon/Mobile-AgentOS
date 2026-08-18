from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_ACTIONS = {
    "click",
    "input",
    "swipe",
    "back",
    "REQUEST_INFORMATION",
    "RESPOND_INFORMATION",
    "REQUEST_OPERATION",
    "RESPOND_OPERATION",
    "FINISH",
}


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class AgentAction:
    action: str
    target_id: int | None = None
    target_text: str | None = None
    text: str | None = None
    direction: str | None = None
    reason: str = ""
    to_agent: str | None = None
    need: str | None = None
    context: str | None = None
    purpose: str | None = None
    resume_instruction: str | None = None
    status: str | None = None
    information: str | None = None
    evidence: str | None = None
    confidence: str | None = None
    limitations: str | None = None
    operation: str | None = None
    expected_result: str | None = None
    result: str | None = None

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "AgentAction":
        action = str(obj.get("action", "")).strip()
        if action not in ALLOWED_ACTIONS:
            raise ActionError(f"unsupported action: {action}")
        target_id = obj.get("target_id")
        if target_id is not None:
            try:
                target_id = int(target_id)
            except (TypeError, ValueError) as exc:
                raise ActionError("target_id must be an integer") from exc
        direction = obj.get("direction")
        if action == "swipe" and direction not in {"up", "down", "left", "right"}:
            raise ActionError("swipe requires direction: up/down/left/right")
        if action == "input" and not obj.get("text"):
            raise ActionError("input requires text")
        if action == "REQUEST_INFORMATION":
            for key in ["to_agent", "need", "context", "purpose", "resume_instruction"]:
                if not obj.get(key):
                    raise ActionError(f"REQUEST_INFORMATION requires {key}")
        if action == "RESPOND_INFORMATION":
            status = str(obj.get("status", "")).strip()
            if status not in {"success", "failed"}:
                raise ActionError("RESPOND_INFORMATION requires status: success|failed")
            if status == "success" and not obj.get("information"):
                raise ActionError("successful RESPOND_INFORMATION requires information")
        if action == "REQUEST_OPERATION":
            for key in ["to_agent", "operation", "context", "purpose", "resume_instruction"]:
                if not obj.get(key):
                    raise ActionError(f"REQUEST_OPERATION requires {key}")
        if action == "RESPOND_OPERATION":
            status = str(obj.get("status", "")).strip()
            if status not in {"success", "failed"}:
                raise ActionError("RESPOND_OPERATION requires status: success|failed")
            if not obj.get("result"):
                raise ActionError("RESPOND_OPERATION requires result")
        if action in {"click", "input"} and target_id is None and not obj.get("target_text"):
            if action == "input":
                # Input may target the first editable field as a fallback.
                pass
            else:
                raise ActionError("click requires target_id or target_text")
        return cls(
            action=action,
            target_id=target_id,
            target_text=str(obj.get("target_text", "")).strip() or None,
            text=str(obj.get("text", "")).strip() or None,
            direction=str(direction).strip() if direction else None,
            reason=str(obj.get("reason", "")).strip(),
            to_agent=str(obj.get("to_agent", "")).strip() or None,
            need=str(obj.get("need", "")).strip() or None,
            context=str(obj.get("context", "")).strip() or None,
            purpose=str(obj.get("purpose", "")).strip() or None,
            resume_instruction=str(obj.get("resume_instruction", "")).strip() or None,
            status=str(obj.get("status", "")).strip() or None,
            information=str(obj.get("information", "")).strip() or None,
            evidence=str(obj.get("evidence", "")).strip() or None,
            confidence=str(obj.get("confidence", "")).strip() or None,
            limitations=str(obj.get("limitations", "")).strip() or None,
            operation=str(obj.get("operation", "")).strip() or None,
            expected_result=str(obj.get("expected_result", "")).strip() or None,
            result=str(obj.get("result", "")).strip() or None,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target_id": self.target_id,
            "target_text": self.target_text,
            "text": self.text,
            "direction": self.direction,
            "reason": self.reason,
            "to_agent": self.to_agent,
            "need": self.need,
            "context": self.context,
            "purpose": self.purpose,
            "resume_instruction": self.resume_instruction,
            "status": self.status,
            "information": self.information,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "limitations": self.limitations,
            "operation": self.operation,
            "expected_result": self.expected_result,
            "result": self.result,
        }
