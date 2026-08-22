from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from ..execution.ui_loop import Observation
from ..graph_space.registry import RegistryTable
from .adb import AdbClient, AdbError
from .accessibility import AccessibilitySnapshotClient
from .ui_tree import UiNode, nodes_from_accessibility_snapshot, prompt_snapshot


@dataclass(frozen=True)
class AppInstance:
    """A concrete AppAgent placement on one Android display."""

    app_id: str
    requested_display_id: int
    screenshot_display_id: str | int | None = None


class AndroidUiDriver:
    """Generic Android observation and primitive-action adapter for AppAgents.

    It retains only the last observation per AppInstance so element identifiers are
    meaningful for exactly one subsequent action. It never searches for a similar
    control when the model action is invalid.
    """

    def __init__(
        self,
        adb: AdbClient,
        registry: RegistryTable,
        instances: dict[str, AppInstance],
        artifact_dir: Path,
        *,
        settle_seconds: float = 0.6,
        snapshots: AccessibilitySnapshotClient | None = None,
    ) -> None:
        self.adb = adb
        self.registry = registry
        self.instances = dict(instances)
        self.artifact_dir = artifact_dir
        self.settle_seconds = settle_seconds
        self.snapshots = snapshots or AccessibilitySnapshotClient(adb)
        self._package_by_app: dict[str, str] = {}
        self._launched: set[str] = set()
        self._last_nodes: dict[str, tuple[UiNode, ...]] = {}
        self._observation_count: defaultdict[str, int] = defaultdict(int)
        self._locks: defaultdict[int, RLock] = defaultdict(RLock)
        self._input_lock = RLock()
        self._surface_lock = RLock()
        self._actual_display_by_app: dict[str, int] = {}
        self._actual_screenshot_by_app: dict[str, str | int | None] = {}
        self._surface_events: list[dict[str, object]] = []

    def observe(self, app_id: str) -> Observation:
        instance = self._instance(app_id)
        self._ensure_launched(app_id)
        display_id = self._actual_display_by_app[app_id]
        screenshot_id = self._actual_screenshot_by_app[app_id]
        with self._locks[display_id]:
            self._observation_count[app_id] += 1
            index = self._observation_count[app_id]
            directory = self.artifact_dir / app_id / f"observe_{index:03d}"
            expected_package = self._package_by_app[app_id]
            snapshot = self._await_snapshot(display_id, expected_package)
            nodes = tuple(nodes_from_accessibility_snapshot(snapshot))
            screenshot = self.adb.screenshot(directory / "screen.png") if screenshot_id is None else self.adb.screenshot_display(screenshot_id, directory / "screen.png")
            observed_packages = {node.package for node in nodes if node.package}
            if expected_package not in observed_packages:
                raise AdbError(
                    f"observation surface for {app_id} contains {sorted(observed_packages)} instead of {expected_package}"
                )
            self._last_nodes[app_id] = nodes
            return Observation(screenshot, prompt_snapshot(list(nodes)))

    def act(self, app_id: str, action: dict[str, Any]) -> None:
        instance = self._instance(app_id)
        self._ensure_launched(app_id)
        display_id = self._actual_display_by_app[app_id]
        with self._locks[display_id]:
            # Android's shell input and IME dispatcher are single shared system paths.
            # The display lock preserves an AppInstance's local order; this lock protects
            # the device-wide input transaction without constraining observation or inference.
            with self._input_lock:
                kind = str(action.get("action", "")).lower()
                if kind == "click":
                    x, y = self._point_for_action(app_id, action)
                    self.adb.tap_display(display_id, x, y)
                    return
                if kind == "input_text":
                    x, y = self._point_for_action(app_id, action)
                    text = action.get("text")
                    if not isinstance(text, str) or not text:
                        raise AdbError("input_text requires non-empty text")
                    self.adb.tap_display(display_id, x, y)
                    self.adb.replace_text_display(display_id, text)
                    return
                if kind == "swipe":
                    direction = action.get("direction")
                    if not isinstance(direction, str):
                        raise AdbError("swipe requires direction")
                    self.adb.swipe_display(display_id, direction)
                    return
                if kind == "back":
                    self.adb.back_display(display_id)
                    return
                raise AdbError(f"unsupported primitive action: {kind or '<empty>'}")

    def settle(self, app_id: str) -> None:
        del app_id
        time.sleep(self.settle_seconds)

    def _instance(self, app_id: str) -> AppInstance:
        try:
            return self.instances[app_id]
        except KeyError as exc:
            raise AdbError(f"no Android AppInstance bound for {app_id}") from exc

    def _ensure_launched(self, app_id: str) -> None:
        if app_id in self._launched:
            return
        with self._surface_lock:
            if app_id in self._launched:
                return
            instance = self._instance(app_id)
            package = self._package_by_app.get(app_id)
            if package is None:
                package = self.adb.pick_package(list(self.registry.get(app_id).package_candidates))
                self._package_by_app[app_id] = package
            if instance.requested_display_id == 0:
                self.adb.launch_package(package)
            else:
                self.adb.launch_package_on_display(package, instance.requested_display_id)
            actual_display = self._resolve_actual_display(package, instance.requested_display_id)
            screenshot_id = (
                instance.screenshot_display_id
                if actual_display == instance.requested_display_id and instance.screenshot_display_id is not None
                else self._screenshot_id_for(actual_display)
            )
            self._actual_display_by_app[app_id] = actual_display
            self._actual_screenshot_by_app[app_id] = screenshot_id
            self._surface_events.append(
                {
                    "app_id": app_id,
                    "package": package,
                    "requested_display": instance.requested_display_id,
                    "actual_observation_surface": actual_display,
                    "display_remapped": actual_display != instance.requested_display_id,
                }
            )
            self._launched.add(app_id)

    def surface_events(self) -> tuple[dict[str, object], ...]:
        return tuple(self._surface_events)

    def _resolve_actual_display(self, package: str, requested_display_id: int) -> int:
        for _ in range(5):
            displays = self.adb.package_display_ids().get(package, [])
            if requested_display_id in displays:
                return requested_display_id
            if displays:
                return displays[0]
            time.sleep(0.25)
        raise AdbError(f"unable to resolve actual observation surface for {package}")

    def _screenshot_id_for(self, logical_display_id: int) -> str | int | None:
        if logical_display_id == 0:
            return None
        for display in self.adb.list_displays():
            if display.display_id == logical_display_id and display.surfaceflinger_id:
                return display.surfaceflinger_id
        raise AdbError(f"no SurfaceFlinger display id for logical display {logical_display_id}")

    def _await_snapshot(self, display_id: int, package: str) -> dict[str, Any]:
        """Wait for the target accessibility window to register after launch or transition."""
        last_error: AdbError | None = None
        for attempt in range(5):
            try:
                return self.snapshots.snapshot(display_id, package)
            except AdbError as exc:
                last_error = exc
                if "no_interactive_windows_on_display" not in str(exc) and "expected_package_not_found" not in str(exc):
                    raise
                if attempt < 4:
                    time.sleep(0.2)
        assert last_error is not None
        raise last_error

    def _point_for_action(self, app_id: str, action: dict[str, Any]) -> tuple[int, int]:
        if "element_id" in action:
            try:
                element_id = int(action["element_id"])
            except (TypeError, ValueError) as exc:
                raise AdbError("element_id must be an integer from the latest observation") from exc
            for node in self._last_nodes.get(app_id, ()):
                if node.index == element_id and node.enabled:
                    return node.action_center or node.bounds.center
            raise AdbError(f"element_id {element_id} is unavailable in the latest observation for {app_id}")
        try:
            return int(action["x"]), int(action["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdbError("click/input_text requires element_id or integer x/y") from exc
