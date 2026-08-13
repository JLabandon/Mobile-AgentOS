from __future__ import annotations

import time
from dataclasses import dataclass

from .actions import AgentAction
from .display import DisplaySlot
from .snapshots import ObservationSnapshot, PendingAction


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    mode: str
    reason: str = ""
    snapshot_age_ms: int = 0


def risk_level_for_action(action: AgentAction) -> str:
    text = " ".join([action.reason or "", action.target_text or "", action.text or ""]).lower()
    if any(term in text for term in ["send", "pay", "delete", "authorize", "submit", "create", "save"]):
        return "high"
    if action.action == "input":
        return "medium"
    return "low"


class ActionGuard:
    def __init__(self, *, max_snapshot_age_ms: int = 10_000) -> None:
        self.max_snapshot_age_ms = max_snapshot_age_ms

    def check(self, pending: PendingAction, snapshot: ObservationSnapshot, slot: DisplaySlot) -> GuardResult:
        age_ms = int((time.monotonic() - snapshot.created_at) * 1000)
        if pending.risk_level == "high" and age_ms > self.max_snapshot_age_ms:
            return GuardResult(False, mode="full_observe_required", reason="high_risk_action", snapshot_age_ms=age_ms)
        if slot.owner_agent != pending.agent:
            return GuardResult(False, mode="fast", reason="display_owner_changed", snapshot_age_ms=age_ms)
        if slot.display_id != snapshot.display_id:
            return GuardResult(False, mode="fast", reason="display_changed", snapshot_age_ms=age_ms)
        if slot.app_package and slot.app_package != snapshot.app_package:
            return GuardResult(False, mode="fast", reason="package_changed", snapshot_age_ms=age_ms)
        if age_ms > self.max_snapshot_age_ms:
            return GuardResult(False, mode="fast", reason="snapshot_too_old", snapshot_age_ms=age_ms)
        mode = "full_observe" if pending.risk_level == "high" else "fast"
        return GuardResult(True, mode=mode, snapshot_age_ms=age_ms)
