from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PIL import Image

from ..execution.prompts import ACTION_JSON_SCHEMA, build_action_prompt as render_action_prompt
from .base import ModelClientError
from .parsing import parse_json_object


class VlmError(ModelClientError):
    pass


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


def load_gemini_key() -> str:
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(key, "").strip()
        if value:
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

    def build_action_prompt(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> str:
        width, height = _model_screenshot_size(screenshot_path)
        return render_action_prompt(width=width, height=height, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)

    def decide_ui_action(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        image = self._types.Part.from_bytes(data=_model_screenshot_bytes(screenshot_path), mime_type="image/png")
        prompt = self.build_action_prompt(screenshot_path=screenshot_path, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image],
            config=self._types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=ACTION_JSON_SCHEMA, temperature=0.0),
        )
        return parse_json_object(response.text or "")


def _model_screenshot_bytes(screenshot_path: Path) -> bytes:
    return screenshot_path.read_bytes()


def _model_screenshot_size(screenshot_path: Path) -> tuple[int, int]:
    try:
        from io import BytesIO

        return Image.open(BytesIO(_model_screenshot_bytes(screenshot_path))).size
    except Exception:
        return Image.open(screenshot_path).size
