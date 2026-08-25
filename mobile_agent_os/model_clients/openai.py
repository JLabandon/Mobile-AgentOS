from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..execution.prompts import build_action_prompt
from .base import ModelClientError
from .parsing import parse_json_object


DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"


class OpenAIClient:
    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.client = OpenAI(api_key=_load_openai_key(), timeout=float(os.environ.get("OPENAI_TIMEOUT", "90")), max_retries=int(os.environ.get("OPENAI_MAX_RETRIES", "1")))

    def generate_text(self, *, system: str, user: str, max_tokens: int = 1200, json_schema: dict[str, Any] | None = None) -> str:
        del json_schema
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "developer", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_completion_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def parse_json_content(self, text: str) -> dict[str, Any]:
        return parse_json_object(text)

    def build_action_prompt(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> str:
        from PIL import Image

        width, height = Image.open(screenshot_path).size
        return build_action_prompt(width=width, height=height, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)

    def decide_ui_action(self, *, screenshot_path: Path, agent_name: str, app_label: str, task_instruction: str, memory: str = "") -> dict[str, Any]:
        return parse_json_object(self._vision_json(screenshot_path, self.build_action_prompt(screenshot_path=screenshot_path, agent_name=agent_name, app_label=app_label, task_instruction=task_instruction, memory=memory)))

    def _vision_json(self, screenshot_path: Path, prompt: str) -> str:
        if not screenshot_path.exists():
            raise ModelClientError(f"screenshot does not exist: {screenshot_path}")
        encoded = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}]}],
            response_format={"type": "json_object"},
            max_completion_tokens=1200,
        )
        return response.choices[0].message.content or ""


def _load_openai_key() -> str:
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if value:
        return value
    raise ModelClientError("missing OPENAI_API_KEY")
