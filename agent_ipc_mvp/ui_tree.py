from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
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
    bounds: Bounds
    clickable: bool
    enabled: bool
    editable: bool

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.class_name

    def to_prompt_dict(self) -> dict[str, Any]:
        x, y = self.bounds.center
        return {
            "id": self.index,
            "text": self.text,
            "content_desc": self.content_desc,
            "resource_id": self.resource_id,
            "class": self.class_name,
            "center": [x, y],
            "clickable": self.clickable,
            "enabled": self.enabled,
            "editable": self.editable,
        }


def parse_bounds(raw: str) -> Bounds:
    nums = [int(value) for value in re.findall(r"\d+", raw)]
    if len(nums) != 4:
        raise ValueError(f"bad bounds: {raw}")
    return Bounds(nums[0], nums[1], nums[2], nums[3])


def parse_ui_xml(path: Path) -> list[UiNode]:
    root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    nodes: list[UiNode] = []
    for element in root.iter("node"):
        attrs = element.attrib
        bounds_raw = attrs.get("bounds")
        if not bounds_raw:
            continue
        text = attrs.get("text", "").strip()
        content_desc = attrs.get("content-desc", "").strip()
        resource_id = attrs.get("resource-id", "").strip()
        class_name = attrs.get("class", "").strip()
        clickable = attrs.get("clickable") == "true"
        enabled = attrs.get("enabled", "true") == "true"
        editable = "EditText" in class_name or attrs.get("password") == "true"
        if not any([text, content_desc, resource_id, clickable, editable]):
            continue
        nodes.append(
            UiNode(
                index=len(nodes),
                text=text,
                content_desc=content_desc,
                resource_id=resource_id,
                class_name=class_name,
                bounds=parse_bounds(bounds_raw),
                clickable=clickable,
                enabled=enabled,
                editable=editable,
            )
        )
    return nodes


def visible_texts(nodes: list[UiNode], limit: int = 80) -> list[str]:
    values: list[str] = []
    for node in nodes:
        for value in [node.text, node.content_desc]:
            if value and value not in values:
                values.append(value)
        if len(values) >= limit:
            break
    return values


def prompt_snapshot(nodes: list[UiNode], limit: int = 100) -> str:
    compact = [node.to_prompt_dict() for node in nodes[:limit]]
    return "\n".join(str(item) for item in compact)


def find_node(
    nodes: list[UiNode],
    *,
    target_id: int | None = None,
    target_text: str | None = None,
    editable_only: bool = False,
) -> UiNode | None:
    if target_id is not None:
        for node in nodes:
            if node.index == target_id:
                return node
    if target_text:
        needle = target_text.lower()
        scored: list[tuple[int, UiNode]] = []
        for node in nodes:
            if editable_only and not node.editable:
                continue
            haystack = " ".join([node.text, node.content_desc, node.resource_id, node.class_name]).lower()
            if needle in haystack:
                score = 0
                if node.clickable:
                    score += 8
                if node.editable:
                    score += 8
                if node.text.lower() == needle:
                    score += 5
                if node.content_desc.lower() == needle:
                    score += 4
                if node.enabled:
                    score += 2
                scored.append((score, node))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            return scored[0][1]
    if editable_only:
        for node in nodes:
            if node.editable and node.enabled:
                return node
    return None
