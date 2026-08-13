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
    timeout: float = 45.0

    @classmethod
    def from_env(cls) -> "LlmConfig":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise LlmError("missing DEEPSEEK_API_KEY")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "45")),
        )


class DeepSeekClient:
    def __init__(self, config: LlmConfig | None = None) -> None:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise LlmError("missing dependency: install with `pip install -r requirements.txt`") from exc
        self.config = config or LlmConfig.from_env()
        self.client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            max_retries=0,
        )

    def raw_chat(self, *, system: str, user: str, max_tokens: int = 600) -> str:
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return response.choices[0].message.content or ""

    def parse_json_content(self, content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for index, char in enumerate(content):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(content[index:])
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise LlmError(f"model did not return valid JSON: {content}") from None
        if not isinstance(parsed, dict):
            raise LlmError(f"model did not return a JSON object: {content}")
        return parsed

    def json_chat(self, *, system: str, user: str, max_tokens: int = 600) -> dict[str, Any]:
        content = self.raw_chat(system=system, user=user, max_tokens=max_tokens)
        return self.parse_json_content(content)

    def smoke_test(self) -> dict[str, Any]:
        return self.json_chat(
            system="Return JSON only.",
            user='Return exactly this JSON object: {"ok": true}',
            max_tokens=50,
        )
