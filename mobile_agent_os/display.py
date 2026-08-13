from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .actions import AgentAction
from .adb import AdbClient
from .snapshots import ObservationSnapshot


@dataclass
class DisplaySlot:
    display_id: int
    owner_agent: str | None = None
    app_package: str | None = None
    observation_channel: str = "mock_accessibility"
    input_supported: bool = True
    screenshot_supported: bool = False
    ui_dump_supported: bool = True
    status: str = "available"


@dataclass(frozen=True)
class ActionResult:
    status: str
    message: str = ""


class DisplayBackedAgent(Protocol):
    name: str

    def display_package(self) -> str:
        ...

    def observe_display(self, display_id: int) -> ObservationSnapshot:
        ...

    def apply_display_action(self, display_id: int, action: AgentAction) -> ActionResult:
        ...


class DisplayManager:
    def __init__(self, slots: list[DisplaySlot] | None = None) -> None:
        self._slots = slots or [DisplaySlot(display_id=0)]

    def list_slots(self) -> list[DisplaySlot]:
        return list(self._slots)

    def allocate(self, agent: str, app_package: str) -> DisplaySlot:
        for slot in self._slots:
            if slot.owner_agent in {None, agent}:
                slot.owner_agent = agent
                slot.app_package = app_package
                slot.status = "allocated"
                return slot
        display_id = max(slot.display_id for slot in self._slots) + 1 if self._slots else 0
        slot = DisplaySlot(display_id=display_id, owner_agent=agent, app_package=app_package, status="allocated")
        self._slots.append(slot)
        return slot

    def slot_for_agent(self, agent: str) -> DisplaySlot:
        for slot in self._slots:
            if slot.owner_agent == agent:
                return slot
        raise KeyError(f"no display slot allocated for {agent}")

    def observe(self, agent: DisplayBackedAgent) -> ObservationSnapshot:
        slot = self.slot_for_agent(agent.name)
        return agent.observe_display(slot.display_id)

    def input(self, agent: DisplayBackedAgent, action: AgentAction) -> ActionResult:
        slot = self.slot_for_agent(agent.name)
        if not slot.input_supported:
            return ActionResult(status="failed", message=f"display {slot.display_id} does not support input")
        return agent.apply_display_action(slot.display_id, action)


class AndroidDisplayManager(DisplayManager):
    def __init__(self, adb: AdbClient, *, include_display0: bool = True) -> None:
        self.adb = adb
        displays = adb.list_displays()
        slots = [
            DisplaySlot(
                display_id=info.display_id,
                observation_channel="uiautomator_display0" if info.display_id == 0 else "surface_screenshot",
                input_supported=True,
                screenshot_supported=info.surfaceflinger_id is not None,
                ui_dump_supported=info.display_id == 0,
                status="available",
            )
            for info in displays
            if include_display0 or info.display_id != 0
        ]
        slots = sorted(slots, key=lambda slot: (slot.display_id == 0, slot.display_id))
        super().__init__(slots or [DisplaySlot(display_id=0)])

    def observe(self, agent: DisplayBackedAgent) -> ObservationSnapshot:
        slot = self.slot_for_agent(agent.name)
        self.adb.launch_package_on_display(agent.display_package(), slot.display_id)
        return agent.observe_display(slot.display_id)


class ForegroundObservationDisplayManager(DisplayManager):
    """ADB/uiautomator can expose one reliable accessibility tree: the foreground display."""

    def __init__(self, adb: AdbClient) -> None:
        self.adb = adb
        self._packages_by_agent: dict[str, str] = {}
        super().__init__([DisplaySlot(display_id=0, observation_channel="foreground_uiautomator", input_supported=True, screenshot_supported=True, ui_dump_supported=True)])

    def allocate(self, agent: str, app_package: str) -> DisplaySlot:
        self._packages_by_agent[agent] = app_package
        return DisplaySlot(
            display_id=0,
            owner_agent=agent,
            app_package=app_package,
            observation_channel="foreground_uiautomator",
            input_supported=True,
            screenshot_supported=True,
            ui_dump_supported=True,
            status="allocated",
        )

    def slot_for_agent(self, agent: str) -> DisplaySlot:
        package = self._packages_by_agent.get(agent)
        if not package:
            raise KeyError(f"no display slot allocated for {agent}")
        return DisplaySlot(
            display_id=0,
            owner_agent=agent,
            app_package=package,
            observation_channel="foreground_uiautomator",
            input_supported=True,
            screenshot_supported=True,
            ui_dump_supported=True,
            status="allocated",
        )

    def list_slots(self) -> list[DisplaySlot]:
        if not self._packages_by_agent:
            return super().list_slots()
        return [self.slot_for_agent(agent) for agent in sorted(self._packages_by_agent)]

    def observe(self, agent: DisplayBackedAgent) -> ObservationSnapshot:
        self.adb.launch_package(agent.display_package())
        return agent.observe_display(0)

    def input(self, agent: DisplayBackedAgent, action: AgentAction) -> ActionResult:
        self.adb.launch_package(agent.display_package())
        return agent.apply_display_action(0, action)
