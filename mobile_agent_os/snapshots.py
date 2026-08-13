from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def stable_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ObservationSnapshot:
    snapshot_id: str
    agent: str
    display_id: int
    app_package: str
    activity: str | None
    created_at: float
    ui_text_digest: str
    visible_text: str = ""
    target_nodes: tuple[dict[str, Any], ...] = ()
    screenshot_path: str | None = None
    xml_path: str | None = None

    @classmethod
    def create(
        cls,
        *,
        agent: str,
        display_id: int,
        app_package: str,
        visible_text: str,
        activity: str | None = None,
        target_nodes: list[dict[str, Any]] | None = None,
        screenshot_path: str | Path | None = None,
        xml_path: str | Path | None = None,
        snapshot_id: str | None = None,
    ) -> "ObservationSnapshot":
        created_at = time.monotonic()
        digest = stable_digest(f"{agent}:{display_id}:{app_package}:{visible_text}:{created_at}")
        return cls(
            snapshot_id=snapshot_id or f"snap_{digest}",
            agent=agent,
            display_id=display_id,
            app_package=app_package,
            activity=activity,
            created_at=created_at,
            ui_text_digest=stable_digest(visible_text),
            visible_text=visible_text,
            target_nodes=tuple(target_nodes or ()),
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            xml_path=str(xml_path) if xml_path else None,
        )


@dataclass(frozen=True)
class PendingDecision:
    decision_id: str
    agent: str
    task_id: str
    snapshot_id: str
    submitted_at: float
    completed_at: float | None = None
    status: str = "pending"
    prompt_hash: str = ""


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    decision_id: str
    agent: str
    action: Any
    snapshot_id: str
    risk_level: str
    target_ref: dict[str, Any] = field(default_factory=dict)


class SnapshotStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, ObservationSnapshot] = {}

    def put(self, snapshot: ObservationSnapshot) -> ObservationSnapshot:
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def get(self, snapshot_id: str) -> ObservationSnapshot:
        return self._snapshots[snapshot_id]

    def latest_for_agent(self, agent: str) -> ObservationSnapshot | None:
        snapshots = [item for item in self._snapshots.values() if item.agent == agent]
        if not snapshots:
            return None
        return max(snapshots, key=lambda item: item.created_at)
