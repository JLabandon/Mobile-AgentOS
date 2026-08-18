from __future__ import annotations

from dataclasses import dataclass

from .app_agents import AppConfig, AppStaffAgent


@dataclass(frozen=True)
class AgentSpec:
    name: str
    app_label: str
    package_candidates: tuple[str, ...]
    capabilities: tuple[str, ...]


class AgentRegistry:
    def __init__(self, agents: dict[str, AppStaffAgent], configs: dict[str, AppConfig]) -> None:
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

    def get(self, name: str) -> AppStaffAgent:
        key = name.removesuffix("_agent")
        return self.agents[key]

    def capabilities_for(self, name: str) -> tuple[str, ...]:
        key = name.removesuffix("_agent")
        spec = self.specs[key]
        return spec.capabilities

    def resolve_capability(self, *, target_capability: str = "", need: str = "", preferred_agent: str = "") -> AgentSpec | None:
        if preferred_agent:
            key = preferred_agent.removesuffix("_agent")
            if key in self.specs:
                return self.specs[key]
        query = " ".join([target_capability, need]).replace("_", " ").lower()
        best: tuple[int, AgentSpec] | None = None
        query_terms = {term for term in query.split() if len(term) >= 3}
        for spec in self.specs.values():
            capability_terms: set[str] = set()
            for capability in spec.capabilities:
                capability_terms.update(term for term in capability.replace("_", " ").lower().split() if len(term) >= 3)
            score = len(query_terms & capability_terms)
            if target_capability and target_capability in spec.capabilities:
                score += 10
            if score and (best is None or score > best[0]):
                best = (score, spec)
        return best[1] if best else None

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
