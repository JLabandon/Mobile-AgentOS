from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mobile_agent_os.execution import Completed, ExecutionContext, Failed, NeedsExpansion
from mobile_agent_os.graph_space import AppProfile, ArtifactDraft, RegistryTable


def registry() -> RegistryTable:
    return RegistryTable(
        {
            "calendar": AppProfile("calendar", "Calendar", "Appointment app", ("create_event",), ("calendar.pkg",)),
            "notes": AppProfile("notes", "Notes", "Note source", ("search_notes", "retrieve_information"), ("notes.pkg",)),
            "payment": AppProfile("payment", "Payment", "Payment app", ("authorize_payment",), ("payment.pkg",)),
        }
    )


@dataclass
class ScriptedExecutor:
    outcomes: list[object]

    def execute(self, context: ExecutionContext) -> object:
        del context
        return self.outcomes.pop(0)


class FakeTextClient:
    model = "fake"

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.last_system = ""
        self.last_user = ""
        self.last_json_schema: dict[str, Any] | None = None

    def generate_text(self, **kwargs: Any) -> str:
        self.last_system = str(kwargs.get("system", ""))
        self.last_user = str(kwargs.get("user", ""))
        self.last_json_schema = kwargs.get("json_schema")
        return "ignored"

    def parse_json_content(self, text: str) -> dict[str, Any]:
        del text
        return self.response
