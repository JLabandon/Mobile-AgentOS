from __future__ import annotations

from mobile_agent_os.agents import AppConfig
from mobile_agent_os.registry import AgentRegistry


class FakeAgent:
    def __init__(self, config: AppConfig) -> None:
        self.config = config


def test_registry_accepts_app_staff_agent_shape() -> None:
    config = AppConfig(
        name="calendar",
        label="Google Calendar",
        package_candidates=["com.google.android.calendar"],
        launch={"mode": "launcher"},
        capabilities=("create_event",),
    )
    agent = FakeAgent(config)
    registry = AgentRegistry({"calendar": agent}, {"calendar": config})  # type: ignore[arg-type]

    assert registry.get("calendar_agent") is agent
    assert registry.trace_payload() == [
        {
            "name": "calendar_agent",
            "app_label": "Google Calendar",
            "package_candidates": ["com.google.android.calendar"],
            "capabilities": ["create_event"],
        }
    ]


def test_registry_resolves_capability_without_app_specific_rules() -> None:
    calendar = FakeAgent(
        AppConfig(
            name="calendar",
            label="Google Calendar",
            package_candidates=["com.google.android.calendar"],
            launch={"mode": "launcher"},
            capabilities=("create_event",),
        )
    )
    maps = FakeAgent(
        AppConfig(
            name="maps",
            label="Google Maps",
            package_candidates=["com.google.android.apps.maps"],
            launch={"mode": "launcher"},
            capabilities=("search_place", "estimate_travel_time", "feasibility_check"),
        )
    )
    registry = AgentRegistry({"calendar": calendar, "maps": maps}, {"calendar": calendar.config, "maps": maps.config})  # type: ignore[arg-type]

    assert registry.resolve_capability(need="estimate travel time to the airport").name == "maps_agent"
    assert registry.resolve_capability(preferred_agent="calendar_agent", need="estimate travel time").name == "calendar_agent"
