from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AppProfile:
    app_id: str
    label: str
    description: str
    capabilities: tuple[str, ...]
    package_candidates: tuple[str, ...]
    long_term_memory: tuple[str, ...] = ()
    default_resources: tuple[str, ...] = ()
    service_capacity: int = 1

    @classmethod
    def from_config(cls, app_id: str, value: dict[str, Any]) -> "AppProfile":
        instance = value.get("instance_policy", {})
        capacity = int(instance.get("max_parallel_instances", 1)) if instance.get("supports_parallel_instances") else 1
        return cls(
            app_id=app_id,
            label=str(value.get("label", app_id)),
            description=str(value.get("description", "")),
            capabilities=tuple(str(item) for item in value.get("capabilities", [])),
            package_candidates=tuple(str(item) for item in value.get("package_candidates", [])),
            long_term_memory=tuple(str(item) for item in value.get("long_term_memory", [])),
            default_resources=tuple(str(item) for item in value.get("default_resources", [])),
            service_capacity=max(1, capacity),
        )

    def prompt_view(self) -> dict[str, Any]:
        return {
            "agent_id": self.app_id,
            "app": self.label,
            "description": self.description,
            "capabilities": list(self.capabilities),
        }


class RegistryTable:
    def __init__(self, profiles: dict[str, AppProfile]) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RegistryTable":
        return cls({app_id: AppProfile.from_config(app_id, value) for app_id, value in config.items()})

    def get(self, app_id: str) -> AppProfile:
        return self._profiles[app_id]

    def profiles(self) -> tuple[AppProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def prompt_rows(self) -> list[dict[str, Any]]:
        return [profile.prompt_view() for profile in self.profiles()]

    def providers(self, capability: str) -> tuple[AppProfile, ...]:
        return tuple(profile for profile in self.profiles() if capability in profile.capabilities)
