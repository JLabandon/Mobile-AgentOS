from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from ..app_agents.actions import AgentAction
from ..android.display import ActionResult, DisplayBackedAgent, DisplayManager, DisplaySlot
from ..report import RunReporter
from ..kernel.snapshots import ObservationSnapshot


@dataclass(frozen=True)
class SwitchResult:
    slot: DisplaySlot
    switched: bool
    elapsed: float


class KernelServiceAgent(DisplayBackedAgent, Protocol):
    name: str


class KernelService:
    """System service boundary between AppAgents and Android app instances.

    The service executes display/session operations and reports low-level runtime
    events. AppAgents keep UI intent, and Scheduler code keeps admit/wait logic.
    """

    def __init__(self, display_manager: DisplayManager, reporter: RunReporter, *, runtime_name: str) -> None:
        self.display_manager = display_manager
        self.reporter = reporter
        self.runtime_name = runtime_name

    def allocate_display(self, agent: KernelServiceAgent, app_package: str) -> DisplaySlot:
        slot = self.display_manager.allocate(agent.name, app_package)
        self.reporter.event(
            "display_slot_allocated",
            runtime=self.runtime_name,
            agent=agent.name,
            display_id=slot.display_id,
            app_package=app_package,
            observation_channel=slot.observation_channel,
        )
        return slot

    def slot_for_agent(self, agent_name: str) -> DisplaySlot:
        return self.display_manager.slot_for_agent(agent_name)

    def list_slots(self) -> list[DisplaySlot]:
        return self.display_manager.list_slots()

    def switch_for_observation(self, agent: KernelServiceAgent, *, action: str | None = None) -> SwitchResult:
        return self._switch(agent, purpose="observe", action=action)

    def switch_for_input(self, agent: KernelServiceAgent, *, action: str | None = None) -> SwitchResult:
        return self._switch(agent, purpose="act", action=action)

    def observe(self, agent: KernelServiceAgent) -> ObservationSnapshot:
        return self.display_manager.capture_observation(agent)

    def act(self, agent: KernelServiceAgent, action: AgentAction) -> ActionResult:
        return self.display_manager.apply_input(agent, action)

    def _switch(self, agent: KernelServiceAgent, *, purpose: str, action: str | None = None) -> SwitchResult:
        started = time.monotonic()
        if purpose == "observe":
            slot, switched = self.display_manager.activate_for_observation(agent)
        else:
            slot, switched = self.display_manager.activate_for_input(agent)
        elapsed = round(time.monotonic() - started, 3)
        if switched:
            self.reporter.state_event(
                agent.name,
                "SWITCH",
                t=round(time.monotonic() - self.reporter.started_monotonic - elapsed, 3),
                runtime=self.runtime_name,
                display_id=slot.display_id,
                purpose=purpose,
                action=action,
            )
            self.reporter.event(
                "display_switch",
                runtime=self.runtime_name,
                agent=agent.name,
                display_id=slot.display_id,
                purpose=purpose,
                action=action,
                elapsed=elapsed,
            )
        return SwitchResult(slot=slot, switched=switched, elapsed=elapsed)
