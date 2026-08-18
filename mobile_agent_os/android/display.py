from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..android.adb import AdbClient
from ..kernel.snapshots import ObservationSnapshot


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

    def activate_display_session(self, display_id: int) -> bool:
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
        self.activate_for_observation(agent)
        return self.capture_observation(agent)

    def activate_for_observation(self, agent: DisplayBackedAgent) -> tuple[DisplaySlot, bool]:
        slot = self.slot_for_agent(agent.name)
        switched = agent.activate_display_session(slot.display_id)
        return slot, switched

    def capture_observation(self, agent: DisplayBackedAgent) -> ObservationSnapshot:
        slot = self.slot_for_agent(agent.name)
        return agent.observe_display(slot.display_id)

    def input(self, agent: DisplayBackedAgent, action: AgentAction) -> ActionResult:
        self.activate_for_input(agent)
        return self.apply_input(agent, action)

    def activate_for_input(self, agent: DisplayBackedAgent) -> tuple[DisplaySlot, bool]:
        slot = self.slot_for_agent(agent.name)
        if not slot.input_supported:
            raise RuntimeError(f"display {slot.display_id} does not support input")
        switched = agent.activate_display_session(slot.display_id)
        return slot, switched

    def apply_input(self, agent: DisplayBackedAgent, action: AgentAction) -> ActionResult:
        slot = self.slot_for_agent(agent.name)
        if not slot.input_supported:
            return ActionResult(status="failed", message=f"display {slot.display_id} does not support input")
        return agent.apply_display_action(slot.display_id, action)


class AndroidDisplayManager(DisplayManager):
    def __init__(self, adb: AdbClient, *, include_display0: bool = True) -> None:
        self.adb = adb
        self._actual_display_by_agent: dict[str, int] = {}
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
        self.activate_for_observation(agent)
        return self.capture_observation(agent)

    def _actual_display_for_agent(self, agent: DisplayBackedAgent) -> int:
        package = agent.display_package()
        actual_display_ids = self.adb.package_display_ids().get(package, [])
        requested = self.slot_for_agent(agent.name).display_id
        if requested in actual_display_ids:
            actual = requested
        elif actual_display_ids:
            actual = actual_display_ids[0]
        else:
            actual = requested
        self._actual_display_by_agent[agent.name] = actual
        return actual

    def capture_observation(self, agent: DisplayBackedAgent) -> ObservationSnapshot:
        actual_display = self._actual_display_for_agent(agent)
        return agent.observe_display(actual_display)

    def activate_for_input(self, agent: DisplayBackedAgent) -> tuple[DisplaySlot, bool]:
        requested_slot = self.slot_for_agent(agent.name)
        actual_display = self._actual_display_by_agent.get(agent.name, requested_slot.display_id)
        if not requested_slot.input_supported:
            raise RuntimeError(f"display {requested_slot.display_id} does not support input")
        switched = agent.activate_display_session(actual_display)
        return DisplaySlot(
            display_id=actual_display,
            owner_agent=requested_slot.owner_agent,
            app_package=requested_slot.app_package,
            observation_channel=requested_slot.observation_channel,
            input_supported=requested_slot.input_supported,
            screenshot_supported=requested_slot.screenshot_supported,
            ui_dump_supported=requested_slot.ui_dump_supported,
            status=requested_slot.status,
        ), switched

    def apply_input(self, agent: DisplayBackedAgent, action: AgentAction) -> ActionResult:
        requested_slot = self.slot_for_agent(agent.name)
        actual_display = self._actual_display_by_agent.get(agent.name, requested_slot.display_id)
        if not requested_slot.input_supported:
            return ActionResult(status="failed", message=f"display {requested_slot.display_id} does not support input")
        return agent.apply_display_action(actual_display, action)


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
        self.activate_for_observation(agent)
        return self.capture_observation(agent)

    def input(self, agent: DisplayBackedAgent, action: AgentAction) -> ActionResult:
        self.activate_for_input(agent)
        return self.apply_input(agent, action)
