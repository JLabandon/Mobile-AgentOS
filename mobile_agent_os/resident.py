from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AgentState = Literal[
    "READY",
    "RUNNING",
    "WAIT_PEER",
    "WAIT_EXTERNAL",
    "DONE",
    "FAILED",
    "IDLE",
]


@dataclass
class ForegroundInteraction:
    owner_agent: str = ""
    package: str = ""
    display_id: int = 0
    focused_node: str = ""
    input_active: bool = False
    last_action: str = ""

    def update(self, *, owner_agent: str, package: str = "", focused_node: str = "", input_active: bool = False, last_action: str = "") -> None:
        self.owner_agent = owner_agent
        if package:
            self.package = package
        self.focused_node = focused_node
        self.input_active = input_active
        self.last_action = last_action

    def to_json(self) -> dict[str, Any]:
        return {
            "owner_agent": self.owner_agent,
            "package": self.package,
            "display_id": self.display_id,
            "focused_node": self.focused_node,
            "input_active": self.input_active,
            "last_action": self.last_action,
        }


@dataclass
class ResidentAgentState:
    agent_name: str
    app_name: str
    capabilities: tuple[str, ...] = ()
    state: AgentState = "IDLE"
    current_goal: str = ""
    session_memory: list[str] = field(default_factory=list)
    long_term_memory: list[str] = field(default_factory=list)
    last_observation: dict[str, Any] = field(default_factory=dict)
    pending_requests: list[str] = field(default_factory=list)
    owned_resources: list[str] = field(default_factory=list)
    yield_status: str = "yieldable"

    def set_state(self, state: AgentState) -> None:
        self.state = state

    def to_json(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "app_name": self.app_name,
            "capabilities": list(self.capabilities),
            "state": self.state,
            "current_goal": self.current_goal,
            "session_memory": list(self.session_memory),
            "long_term_memory": list(self.long_term_memory),
            "last_observation": dict(self.last_observation),
            "pending_requests": list(self.pending_requests),
            "owned_resources": list(self.owned_resources),
            "yield_status": self.yield_status,
        }
