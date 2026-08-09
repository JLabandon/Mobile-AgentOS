from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_ACTIONS = {"click", "input", "swipe", "back", "FINISH"}


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
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target_id": self.target_id,
            "target_text": self.target_text,
            "text": self.text,
            "direction": self.direction,
            "reason": self.reason,
        }
