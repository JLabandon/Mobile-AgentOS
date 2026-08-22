from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True)
class UiNode:
    index: int
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    package: str
    bounds: Bounds
    clickable: bool
    enabled: bool
    editable: bool
    checkable: bool
    checked: bool
    selected: bool
    focused: bool
    action_center: tuple[int, int] | None = None

    @property
    def label(self) -> str:
        if self.text:
            return self.text
        if self.content_desc:
            return self.content_desc
        if self.resource_id and (self.clickable or self.editable or self.focused):
            return self.resource_id.split("/")[-1].replace("_", " ")
        return self.class_name

    def to_prompt_dict(self) -> dict[str, Any]:
        x, y = self.bounds.center
        action_x, action_y = self.action_center or (x, y)
        return {
            "id": self.index,
            "label": self.label,
            "text": self.text,
            "content_desc": self.content_desc,
            "resource_id": self.resource_id,
            "class": self.class_name,
            "package": self.package,
            "bounds": [self.bounds.left, self.bounds.top, self.bounds.right, self.bounds.bottom],
            "center": [x, y],
            "action_center": [action_x, action_y],
            "clickable": self.clickable,
            "enabled": self.enabled,
            "editable": self.editable,
            "checkable": self.checkable,
            "checked": self.checked,
            "selected": self.selected,
            "focused": self.focused,
        }


def nodes_from_accessibility_snapshot(payload: dict[str, Any]) -> list[UiNode]:
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("accessibility snapshot has no node list")
    nodes: list[UiNode] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        bounds = raw.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        try:
            rect = Bounds(*(int(value) for value in bounds))
        except (TypeError, ValueError):
            continue
        nodes.append(
            UiNode(
                index=len(nodes),
                text=str(raw.get("text", "")).strip(),
                content_desc=str(raw.get("content_desc", "")).strip(),
                resource_id=str(raw.get("resource_id", "")).strip(),
                class_name=str(raw.get("class_name", "")).strip(),
                package=str(raw.get("package", "")).strip(),
                bounds=rect,
                clickable=bool(raw.get("clickable", False)),
                enabled=bool(raw.get("enabled", True)),
                editable=bool(raw.get("editable", False)),
                checkable=bool(raw.get("checkable", False)),
                checked=bool(raw.get("checked", False)),
                selected=bool(raw.get("selected", False)),
                focused=bool(raw.get("focused", False)),
            )
        )
    if not nodes:
        raise ValueError("accessibility snapshot contains no usable nodes")
    return nodes


def prompt_snapshot(nodes: list[UiNode], limit: int = 100) -> str:
    compact = [node.to_prompt_dict() for node in nodes[:limit]]
    return "\n".join(str(item) for item in compact)
