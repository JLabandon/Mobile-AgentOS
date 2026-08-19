from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..android.adb import AdbClient, AdbError


@dataclass(frozen=True)
class DemoAgent:
    name: str
    app_label: str
    package: str
    display_id: int
    surfaceflinger_id: str | None = None
    description: str = ""
    capabilities: tuple[str, ...] = ()
    long_term_memory: tuple[str, ...] = ()
    status_oracle: dict[str, str] | None = None


def capture_agent_screen(adb: AdbClient, agent: DemoAgent, out_path: Path) -> Path:
    if agent.display_id == 0:
        return adb.screenshot(out_path)
    try:
        return adb.screenshot_display(agent.display_id, out_path)
    except AdbError:
        pass
    if agent.surfaceflinger_id:
        return adb.screenshot_display(agent.surfaceflinger_id, out_path)
    raise AdbError(f"failed to capture display {agent.display_id}")
