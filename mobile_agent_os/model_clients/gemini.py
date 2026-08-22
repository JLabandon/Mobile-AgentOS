from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from PIL import Image

from ..execution.prompts import ACTION_JSON_SCHEMA, COMPLETION_REPORT_JSON_SCHEMA, build_action_prompt as render_action_prompt
from ..execution.prompts import build_information_response_prompt as render_information_response_prompt
from ..execution.prompts import build_completion_report_prompt
from .base import ModelClientError, ScreenInspectionResult
from .parsing import parse_json_object


class VlmError(ModelClientError):
    pass


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


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


class GeminiScreenClient:
    def __init__(self, *, model: str | None = None) -> None:
        try:
            from google import genai
            from google.genai import types
        except ModuleNotFoundError as exc:
            raise VlmError("missing dependency: google-genai") from exc
        self._types = types
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.client = genai.Client(api_key=load_gemini_key())

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        prompt = f"{system}\n\n{user}"
        config = {
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "temperature": 0.0,
        }
        if json_schema is not None:
            config["response_json_schema"] = json_schema
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt],
            config=self._types.GenerateContentConfig(**config),
        )
        return response.text or ""

    def parse_json_content(self, text: str) -> dict[str, Any]:
        return parse_json_object(text)

    def inspect_screen(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_context: str) -> ScreenInspectionResult:
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
        return ScreenInspectionResult(text=response.text or "", model=self.model)

    def build_action_prompt(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> str:
        width, height = _model_screenshot_size(screenshot_path)
        return render_action_prompt(width=width, height=height, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)

    def build_information_response_prompt(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> str:
        width, height = _model_screenshot_size(screenshot_path)
        return render_information_response_prompt(width=width, height=height, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)

    def decide_ui_action(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=_model_screenshot_bytes(screenshot_path), mime_type="image/png")
        prompt = self.build_action_prompt(screenshot_path=screenshot_path, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image],
            config=self._types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=ACTION_JSON_SCHEMA, temperature=0.0),
        )
        return parse_json_object(response.text or "")

    def decide_completion_report(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, artifact_kind: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=_model_screenshot_bytes(screenshot_path), mime_type="image/png")
        prompt = build_completion_report_prompt(
            agent_name=agent_name,
            app_label=app_label,
            task_instruction=task_instruction,
            artifact_kind=artifact_kind,
            memory=memory,
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image],
            config=self._types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=COMPLETION_REPORT_JSON_SCHEMA,
                temperature=0.0,
            ),
        )
        return parse_json_object(response.text or "")

    def decide_information_response(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=_model_screenshot_bytes(screenshot_path), mime_type="image/png")
        prompt = self.build_information_response_prompt(screenshot_path=screenshot_path, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)
        response = self.client.models.generate_content(model=self.model, contents=[prompt, image])
        return parse_json_object(response.text or "")


def _model_screenshot_bytes(screenshot_path: Path) -> bytes:
    return screenshot_path.read_bytes()


def _model_screenshot_size(screenshot_path: Path) -> tuple[int, int]:
    try:
        from io import BytesIO

        return Image.open(BytesIO(_model_screenshot_bytes(screenshot_path))).size
    except Exception:
        return Image.open(screenshot_path).size


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
