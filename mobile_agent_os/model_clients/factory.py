from __future__ import annotations

import os

from .base import ScreenModelClient, TextModelClient
from .deepseek import DeepSeekTextClient
from .gemini import GeminiScreenClient
from .openai import OpenAIClient


def create_text_model_client(*, provider: str | None = None, model: str | None = None) -> TextModelClient:
    selected = (provider or os.environ.get("MOBILE_AGENT_OS_TEXT_MODEL_PROVIDER") or "gemini").strip().lower()
    if selected in {"openai", "chatgpt"}:
        return OpenAIClient(model=model or os.environ.get("MOBILE_AGENT_OS_TEXT_MODEL") or os.environ.get("OPENAI_MODEL") or None)
    if selected == "gemini":
        return GeminiScreenClient(model=model or os.environ.get("MOBILE_AGENT_OS_TEXT_MODEL") or os.environ.get("GEMINI_MODEL") or None)
    if selected == "deepseek":
        return DeepSeekTextClient(model=model or os.environ.get("MOBILE_AGENT_OS_TEXT_MODEL") or os.environ.get("DEEPSEEK_MODEL") or None)
    raise ValueError(f"unsupported text model provider: {selected}")


def create_screen_model_client(*, provider: str | None = None, model: str | None = None) -> ScreenModelClient:
    selected = (provider or os.environ.get("MOBILE_AGENT_OS_SCREEN_MODEL_PROVIDER") or "gemini").strip().lower()
    if selected in {"openai", "chatgpt"}:
        return OpenAIClient(model=model or os.environ.get("MOBILE_AGENT_OS_SCREEN_MODEL") or os.environ.get("OPENAI_MODEL") or None)
    if selected == "gemini":
        return GeminiScreenClient(model=model or os.environ.get("MOBILE_AGENT_OS_SCREEN_MODEL") or os.environ.get("GEMINI_MODEL") or None)
    raise ValueError(f"unsupported screen model provider: {selected}")
