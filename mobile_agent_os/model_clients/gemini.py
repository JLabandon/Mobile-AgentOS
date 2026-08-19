from __future__ import annotations

import os
import json
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


class VlmError(RuntimeError):
    pass


def load_gemini_key() -> str:
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    for parent in Path(__file__).resolve().parents:
        key_file = parent / "research_materials" / "markdown" / "mobile_agent_os" / "methods" / "Gemini API"
        if key_file.exists():
            value = key_file.read_text(encoding="utf-8").strip()
            if value:
                os.environ.setdefault("GOOGLE_API_KEY", value)
                return value
    raise VlmError("missing GEMINI_API_KEY or GOOGLE_API_KEY")


@dataclass(frozen=True)
class VlmScreenResult:
    text: str
    model: str


class GeminiScreenClient:
    def __init__(self, *, model: str | None = None) -> None:
        try:
            from google import genai
            from google.genai import types
        except ModuleNotFoundError as exc:
            raise VlmError("missing dependency: google-genai") from exc
        self._types = types
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        self.client = genai.Client(api_key=load_gemini_key())

    def inspect_screen(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_context: str) -> VlmScreenResult:
        if not screenshot_path.exists():
            raise VlmError(f"screenshot does not exist: {screenshot_path}")
        image = self._types.Part.from_bytes(data=screenshot_path.read_bytes(), mime_type="image/png")
        prompt = (
            "You are the visual observer for a mobile app-oriented agent. "
            "Inspect the screenshot and return a concise JSON object with keys: "
            "app_visible, screen_summary, relevant_visible_information, next_observation_needed. "
            "Do not invent information that is not visible.\n\n"
            f"Agent: {agent_name}\n"
            f"Expected app: {app_label}\n"
            f"Task context: {task_context}"
        )
        response = self.client.models.generate_content(model=self.model, contents=[prompt, image])
        return VlmScreenResult(text=response.text or "", model=self.model)

    def build_action_prompt(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        memory: str = "",
    ) -> str:
        width, height = _model_screenshot_size(screenshot_path)
        service_prefix = ""
        if "late-bound runtime information request" in task_instruction.lower():
            service_prefix = (
                "This is an information-answering service request. "
                "Your first choice should be complete, not navigation, when the screenshot already contains text that answers the request. "
                "Do not click search, menu, sort, or a note/card merely to inspect more detail when the visible text is enough. "
                "Use click/back only when the current screenshot does not contain enough evidence to answer.\n\n"
            )
        return (
            service_prefix +
            "You control a mobile app using only primitive UI actions. "
            "Inspect the screenshot and return one JSON object only. "
            "If the visible screen already shows that the assigned task goal is satisfied, use complete with visible evidence instead of clicking more controls. "
            "Complete means this agent's assigned app-specific work is done; do not request another peer merely to finish that peer's own assigned work. "
            "Use request_information or request_operation only when the current assigned run cannot make progress without a peer result. "
            "If the current run is to inspect, prepare, retrieve, or report information for another run, complete with the visible result instead of creating a runtime request. "
            "If memory/context lists a scheduler dependency that will already provide the needed result, wait for that delivered result by completing the current producer work rather than creating a duplicate request. "
            "A final status such as completed, approved, ready, saved, scheduled, or done is stronger evidence than the continued presence of a button. "
            "If your previous action changed the screen into a successful final state, complete instead of pressing the same control again. "
            "If a recent click did not change the visible state, do not repeat the same point; choose a clearer target area, use click_area for the visible control, or fail with evidence if no reliable target exists. "
            "Allowed actions: click_element, click, click_area, input_text, swipe, back, complete, fail, request_information, request_operation. "
            "When memory/context lists Visible UI elements from Android accessibility tree, prefer element-based actions over raw coordinates. "
            "For a listed button or clickable control, return {\"action\":\"click_element\",\"element_id\":<id>} using the matching element id. "
            "For a listed editable field, return {\"action\":\"input_text\",\"element_id\":<id>,\"text\":\"...\"}. "
            "For click, provide integer x and y in screenshot pixel coordinates. "
            "For click_area, provide integer x1, y1, x2, y2 bounding the visible target region; prefer click_area for buttons, large list rows, cards, and other controls where the full area is visible. "
            "For swipe, provide direction as up, down, left, or right; use swipe when the needed control or content is likely outside the current viewport. "
            "The screenshot includes a light coordinate grid copied from MobileRun's vision coordinate contract; use it only as a coordinate reference. "
            "For input_text, provide text and the target input field coordinates when the field is visible: "
            "{\"action\":\"input_text\",\"text\":\"...\",\"x\":100,\"y\":200}. "
            "The coordinate must be inside the editable field body, near its placeholder text or current value, not on a label, reference text, or nearby status text. "
            "The executor will tap that field on the same display and then type the text as one atomic action. "
            "If memory says text was typed but the current screenshot still shows an empty field, use input_text again with a better coordinate inside the field. "
            "Use input_text without coordinates only when the field is already reliably focused in the current step. "
            f"The screenshot size is {width}x{height}; x ranges from 0 to {width - 1}, y ranges from 0 to {height - 1}. "
            "Do not use normalized 0-1000 coordinates. "
            "For complete or fail, provide message. "
            "For request_information, provide target_agent, need, reason, and resume_instruction. "
            "For request_operation, provide target_agent, operation, expected_result, reason, and resume_instruction. "
            "For request_information or request_operation, choose target_agent only from the available peer agents listed in memory/context. "
            "For request_information, choose a peer whose registry capabilities or description indicate information retrieval, search, reading, notes, email, maps, calendar, or other data-provider behavior. "
            "Do not choose a peer merely because it is another task/form app unless its registry profile says it can provide the requested information. "
            "For request_information, describe the missing real-world field in need; do not include internal test names, run labels, or benchmark labels unless they are part of the user's data. "
            "If your task is to answer another agent's information request and the requested information is visible, use complete with the exact visible answer and evidence. "
            "Do not use markdown.\n\n"
            f"Agent: {agent_name}\n"
            f"Expected app: {app_label}\n"
            f"Task: {task_instruction}\n"
            f"Memory/context: {memory or '<none>'}\n"
        )

    def build_information_response_prompt(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        memory: str = "",
    ) -> str:
        width, height = _model_screenshot_size(screenshot_path)
        return (
            "You are handling a runtime information request from another mobile app agent. "
            "Inspect the current screenshot first. Return one JSON object only.\n"
            "If the screenshot contains enough information to answer the request, return: "
            "{\"action\":\"complete\",\"message\":\"<exact answer plus visible evidence>\"}. "
            "If the screenshot does not contain enough information, return: "
            "{\"action\":\"continue_navigation\",\"reason\":\"<what is missing>\"}. "
            "The screen does not need to contain the exact request wording; semantic matches such as a visible field label and value are enough. "
            "For field lookup requests, confirm the answer when the visible evidence covers the requested field and the important qualifiers needed to identify the record. "
            "If record-identifying qualifiers such as person, project, date, time, flight, order, or document title are missing, continue navigation or fail instead of guessing. "
            "Do not click, search, open menus, or navigate in this response. "
            f"The screenshot size is {width}x{height}.\n\n"
            f"Agent: {agent_name}\n"
            f"Expected app: {app_label}\n"
            f"Request: {task_instruction}\n"
            f"Memory/context: {memory or '<none>'}\n"
        )

    def decide_ui_action(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=_model_screenshot_bytes(screenshot_path), mime_type="image/png")
        prompt = self.build_action_prompt(
            screenshot_path=screenshot_path,
            agent_name=agent_name,
            app_label=app_label,
            task_instruction=task_instruction,
            memory=memory,
        )
        response = self.client.models.generate_content(model=self.model, contents=[prompt, image])
        return _parse_json_object(response.text or "")

    def decide_information_response(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=_model_screenshot_bytes(screenshot_path), mime_type="image/png")
        prompt = self.build_information_response_prompt(
            screenshot_path=screenshot_path,
            agent_name=agent_name,
            app_label=app_label,
            task_instruction=task_instruction,
            memory=memory,
        )
        response = self.client.models.generate_content(model=self.model, contents=[prompt, image])
        return _parse_json_object(response.text or "")


def _model_screenshot_bytes(screenshot_path: Path) -> bytes:
    image = screenshot_path.read_bytes()
    try:
        vendor_root = Path(__file__).resolve().parents[2] / "vendor" / "mobilerun_runtime"
        if str(vendor_root) not in sys.path:
            sys.path.insert(0, str(vendor_root))
        from mobilerun.tools.helpers.images import resize_image_to_max_side_with_grid
    except Exception:
        return image
    grid = resize_image_to_max_side_with_grid(image)
    grid_path = screenshot_path.with_name(f"{screenshot_path.stem}_model_grid{screenshot_path.suffix}")
    try:
        grid_path.write_bytes(grid)
    except OSError:
        pass
    return grid


def _model_screenshot_size(screenshot_path: Path) -> tuple[int, int]:
    try:
        from io import BytesIO

        return Image.open(BytesIO(_model_screenshot_bytes(screenshot_path))).size
    except Exception:
        return Image.open(screenshot_path).size


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise VlmError(f"model did not return JSON: {text}") from None
    if not isinstance(value, dict):
        raise VlmError(f"model did not return a JSON object: {text}")
    if "action" not in value:
        status = str(value.get("status", "")).strip().lower()
        if status in {"complete", "completed", "done", "success"}:
            return {"action": "complete", "message": str(value.get("message", "completed"))}
        if status in {"fail", "failed", "error"}:
            return {"action": "fail", "message": str(value.get("message", "failed"))}
        if isinstance(value.get("complete"), str):
            return {"action": "complete", "message": value["complete"]}
        if isinstance(value.get("complete"), dict):
            return {"action": "complete", "message": str(value["complete"].get("message", "completed"))}
        if isinstance(value.get("fail"), str):
            return {"action": "fail", "message": value["fail"]}
        if isinstance(value.get("click"), list) and len(value["click"]) == 2:
            return {"action": "click", "x": value["click"][0], "y": value["click"][1]}
        if isinstance(value.get("click"), dict) and "x" in value["click"] and "y" in value["click"]:
            return {"action": "click", "x": value["click"]["x"], "y": value["click"]["y"]}
        if isinstance(value.get("click_area"), list) and len(value["click_area"]) == 4:
            return {"action": "click_area", "x1": value["click_area"][0], "y1": value["click_area"][1], "x2": value["click_area"][2], "y2": value["click_area"][3]}
        if isinstance(value.get("click_area"), dict):
            area = value["click_area"]
            if all(key in area for key in ("x1", "y1", "x2", "y2")):
                return {"action": "click_area", "x1": area["x1"], "y1": area["y1"], "x2": area["x2"], "y2": area["y2"]}
        if isinstance(value.get("input_text"), str):
            return {"action": "input_text", "text": value["input_text"]}
        if isinstance(value.get("input_text"), dict):
            payload = {"action": "input_text", "text": str(value["input_text"].get("text", ""))}
            if "x" in value["input_text"] and "y" in value["input_text"]:
                payload.update({"x": value["input_text"]["x"], "y": value["input_text"]["y"]})
            return payload
        if isinstance(value.get("type_text"), str):
            return {"action": "input_text", "text": value["type_text"]}
        if "back" in value:
            return {"action": "back"}
        if isinstance(value.get("swipe"), str):
            return {"action": "swipe", "direction": value["swipe"]}
        if isinstance(value.get("scroll"), str):
            direction = "up" if value["scroll"].lower() in {"down", "up"} else value["scroll"]
            return {"action": "swipe", "direction": direction}
        if isinstance(value.get("request_information"), dict):
            return {"action": "request_information", **value["request_information"]}
        if isinstance(value.get("request_operation"), dict):
            return {"action": "request_operation", **value["request_operation"]}
    if (value.get("action") == "click" or value.get("type") == "click") and isinstance(value.get("point"), list) and len(value["point"]) == 2:
        return {"action": "click", "x": value["point"][0], "y": value["point"][1]}
    if value.get("action") == "click_area" and all(key in value for key in ("x1", "y1", "x2", "y2")):
        return {"action": "click_area", "x1": value["x1"], "y1": value["y1"], "x2": value["x2"], "y2": value["y2"]}
    if value.get("action") == "click_area" and isinstance(value.get("area"), list) and len(value["area"]) == 4:
        return {"action": "click_area", "x1": value["area"][0], "y1": value["area"][1], "x2": value["area"][2], "y2": value["area"][3]}
    if value.get("action") in {"scroll_up", "scroll_down", "scroll_left", "scroll_right"}:
        return {"action": "swipe", "direction": str(value["action"]).removeprefix("scroll_")}
    if value.get("action") == "scroll":
        return {"action": "swipe", "direction": str(value.get("direction", "up"))}
    return value
