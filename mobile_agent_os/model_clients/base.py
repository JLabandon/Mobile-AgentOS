from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ModelClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScreenInspectionResult:
    text: str
    model: str


class TextModelClient(Protocol):
    model: str

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        ...

    def parse_json_content(self, text: str) -> dict[str, Any]:
        ...


class ScreenModelClient(TextModelClient, Protocol):
    def inspect_screen(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_context: str,
    ) -> ScreenInspectionResult:
        ...

    def build_action_prompt(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        memory: str = "",
    ) -> str:
        ...

    def build_information_response_prompt(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        memory: str = "",
    ) -> str:
        ...

    def decide_ui_action(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        memory: str = "",
    ) -> dict[str, Any]:
        ...

    def decide_completion_report(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        artifact_kind: str,
        memory: str = "",
    ) -> dict[str, Any]:
        ...

    def decide_information_response(
        self,
        *,
        screenshot_path: Path,
        agent_name: str,
        app_label: str,
        task_instruction: str,
        memory: str = "",
    ) -> dict[str, Any]:
        ...
