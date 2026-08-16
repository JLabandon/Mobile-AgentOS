from __future__ import annotations

import os
import json
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

    def decide_ui_action(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=screenshot_path.read_bytes(), mime_type="image/png")
        width, height = Image.open(screenshot_path).size
        prompt = (
            "You control a mobile app using only primitive UI actions. "
            "Inspect the screenshot and return one JSON object only. "
            "Allowed actions: click, back, complete, fail. "
            "For click, provide integer x and y in screenshot pixel coordinates. "
            f"The screenshot size is {width}x{height}; x ranges from 0 to {width - 1}, y ranges from 0 to {height - 1}. "
            "Do not use normalized 0-1000 coordinates. "
            "When clicking a button, use the visual center of the full button rectangle, not the text baseline. "
            "For complete or fail, provide message. "
            "Do not use markdown.\n\n"
            f"Agent: {agent_name}\n"
            f"Expected app: {app_label}\n"
            f"Task: {task_instruction}\n"
            f"Memory/context: {memory or '<none>'}\n"
        )
        response = self.client.models.generate_content(model=self.model, contents=[prompt, image])
        return _parse_json_object(response.text or "")


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
        if isinstance(value.get("complete"), str):
            return {"action": "complete", "message": value["complete"]}
        if isinstance(value.get("fail"), str):
            return {"action": "fail", "message": value["fail"]}
        if isinstance(value.get("click"), list) and len(value["click"]) == 2:
            return {"action": "click", "x": value["click"][0], "y": value["click"][1]}
    return value
