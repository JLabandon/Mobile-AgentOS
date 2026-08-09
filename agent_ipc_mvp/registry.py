from __future__ import annotations

from dataclasses import dataclass

from .agents import AppConfig, StaffAgent


@dataclass(frozen=True)
class AgentSpec:
    name: str
    app_label: str
    package_candidates: tuple[str, ...]
    capabilities: tuple[str, ...]


class AgentRegistry:
    def __init__(self, agents: dict[str, StaffAgent], configs: dict[str, AppConfig]) -> None:
        self.agents = agents
        self.specs = {
            name: AgentSpec(
                name=f"{name}_agent",
                app_label=config.label,
                package_candidates=tuple(config.package_candidates),
                capabilities=tuple(config.capabilities),
            )
            for name, config in configs.items()
        }

    def get(self, name: str) -> StaffAgent:
        key = name.removesuffix("_agent")
        return self.agents[key]

    def trace_payload(self) -> list[dict[str, object]]:
        return [
            {
                "name": spec.name,
                "app_label": spec.app_label,
                "package_candidates": list(spec.package_candidates),
                "capabilities": list(spec.capabilities),
            }
            for spec in self.specs.values()
        ]
