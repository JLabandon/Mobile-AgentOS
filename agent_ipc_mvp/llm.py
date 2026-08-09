from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


class LlmError(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"

    @classmethod
    def from_env(cls) -> "LlmConfig":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise LlmError("missing DEEPSEEK_API_KEY")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
        )


class DeepSeekClient:
    def __init__(self, config: LlmConfig | None = None) -> None:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise LlmError("missing dependency: install with `pip install -r requirements.txt`") from exc
        self.config = config or LlmConfig.from_env()
        self.client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)

    def json_chat(self, *, system: str, user: str, max_tokens: int = 600) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmError(f"model did not return valid JSON: {content}") from exc

    def smoke_test(self) -> dict[str, Any]:
        return self.json_chat(
            system="Return JSON only.",
            user='Return exactly this JSON object: {"ok": true}',
            max_tokens=50,
        )
