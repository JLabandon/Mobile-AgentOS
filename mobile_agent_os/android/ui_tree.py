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


def parse_bounds(raw: str) -> Bounds:
    nums = [int(value) for value in re.findall(r"\d+", raw)]
    if len(nums) != 4:
        raise ValueError(f"bad bounds: {raw}")
    return Bounds(nums[0], nums[1], nums[2], nums[3])


def parse_ui_xml(path: Path) -> list[UiNode]:
    root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    nodes: list[UiNode] = []

    def visit(element: ET.Element) -> None:
        attrs = element.attrib
        bounds_raw = attrs.get("bounds")
        if bounds_raw:
            text = attrs.get("text", "").strip()
            content_desc = attrs.get("content-desc", "").strip()
            resource_id = attrs.get("resource-id", "").strip()
            class_name = attrs.get("class", "").strip()
            package = attrs.get("package", "").strip()
            clickable = attrs.get("clickable") == "true"
            enabled = attrs.get("enabled", "true") == "true"
            editable = "EditText" in class_name or attrs.get("password") == "true"
            checkable = attrs.get("checkable") == "true"
            checked = attrs.get("checked") == "true"
            selected = attrs.get("selected") == "true"
            focused = attrs.get("focused") == "true"
            focusable = attrs.get("focusable") == "true"
            child_label, child_point = descendant_label_and_point(element)
            clickable_child = has_clickable_descendant(element)
            synthesized_desc = child_label if clickable and not text and not content_desc else content_desc
            if any([text, synthesized_desc, clickable, editable, focusable]):
                if text or synthesized_desc or not resource_id or any([clickable, editable, focused, focusable]):
                    nodes.append(
                        UiNode(
                            index=len(nodes),
                            text=text,
                            content_desc=synthesized_desc,
                            resource_id=resource_id,
                            class_name=class_name,
                            package=package,
                            bounds=parse_bounds(bounds_raw),
                            clickable=clickable,
                            enabled=enabled,
                            editable=editable,
                            checkable=checkable,
                            checked=checked,
                            selected=selected,
                            focused=focused,
                            action_center=child_point if clickable and child_point and not clickable_child else None,
                        )
                    )
        for child in element:
            if child.tag == "node":
                visit(child)

    for child in root:
        if child.tag == "node":
            visit(child)
    return nodes


def descendant_label_and_point(element: ET.Element) -> tuple[str, tuple[int, int] | None]:
    texts: list[str] = []
    first_text_bounds: Bounds | None = None
    for child in element.iter("node"):
        if child is element:
            continue
        value = child.attrib.get("text", "").strip() or child.attrib.get("content-desc", "").strip()
        bounds_raw = child.attrib.get("bounds", "")
        if value:
            texts.append(value)
            if first_text_bounds is None and bounds_raw:
                try:
                    first_text_bounds = parse_bounds(bounds_raw)
                except ValueError:
                    pass
    label = " ".join(texts[:4])
    point = None
    if first_text_bounds:
        point = (min(first_text_bounds.right, first_text_bounds.left + 28), first_text_bounds.center[1])
    return label, point


def has_clickable_descendant(element: ET.Element) -> bool:
    for child in element.iter("node"):
        if child is element:
            continue
        if child.attrib.get("clickable") == "true":
            return True
    return False


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
            if node.index == target_id and (not editable_only or node.editable):
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
