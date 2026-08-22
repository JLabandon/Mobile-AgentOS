from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from .base import ModelClientError
from .parsing import parse_json_object


class DeepSeekTextClient:
    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.client = OpenAI(
            api_key=_load_deepseek_key(),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "60")),
            max_retries=int(os.environ.get("DEEPSEEK_MAX_RETRIES", "1")),
        )

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        del json_schema
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=_deepseek_extra_body(),
        )
        return response.choices[0].message.content or ""

    def parse_json_content(self, text: str) -> dict[str, Any]:
        return parse_json_object(text)


def _deepseek_extra_body() -> dict[str, Any]:
    if os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower() in {"enabled", "true", "1"}:
        body: dict[str, Any] = {"thinking": {"type": "enabled"}}
        effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "").strip()
        if effort:
            body["reasoning_effort"] = effort
        return body
    return {"thinking": {"type": "disabled"}}


def _load_deepseek_key() -> str:
    value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value
    for parent in Path(__file__).resolve().parents:
        for candidate in (
            parent / ".env",
            parent / "projects" / "agent_ipc_mvp" / ".env",
            parent / "agent_ipc_mvp" / ".env",
        ):
            if not candidate.exists():
                continue
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, item = line.split("=", 1)
                if key.strip() == "DEEPSEEK_API_KEY":
                    value = item.strip().strip('"').strip("'")
                    if value:
                        os.environ.setdefault("DEEPSEEK_API_KEY", value)
                        return value
    raise ModelClientError("missing DEEPSEEK_API_KEY")
