from pathlib import Path

import pytest

from mobile_agent_os.android.driver import AndroidUiDriver, AppInstance
from mobile_agent_os.graph_space import AppProfile, RegistryTable


class FakeAdb:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def pick_package(self, candidates):
        self.calls.append(("pick", tuple(candidates)))
        return candidates[0]

    def launch_package(self, package):
        self.calls.append(("launch", package))

    def launch_package_on_display(self, package, display_id):
        self.calls.append(("launch-display", package, display_id))

    def package_display_ids(self):
        return getattr(self, "actual_displays", {"test.app": [2]})

    def list_displays(self):
        return []

    def screenshot_display(self, display_id, path):
        self.calls.append(("screenshot", display_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    def screenshot(self, path):
        self.calls.append(("screenshot-primary",))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    def tap_display(self, display_id, x, y):
        self.calls.append(("tap", display_id, x, y))

    def replace_text_display(self, display_id, text):
        self.calls.append(("replace", display_id, text))


class FakeSnapshots:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def snapshot(self, display_id: int, package: str):
        self.calls.append((display_id, package))
        return {
            "ok": True,
            "display_id": display_id,
            "package": package,
            "nodes": [{
                "text": "Save",
                "content_desc": "",
                "resource_id": "test:id/save",
                "class_name": "android.widget.Button",
                "package": package,
                "bounds": [10, 20, 110, 60],
                "clickable": True,
                "enabled": True,
                "editable": False,
                "checkable": False,
                "checked": False,
                "selected": False,
                "focused": False,
            }],
        }


def _registry() -> RegistryTable:
    return RegistryTable({"app": AppProfile("app", "App", "Test", ("work",), ("test.app",))})


def test_android_driver_uses_latest_element_id_without_fuzzy_fallback(tmp_path: Path) -> None:
    adb = FakeAdb()
    snapshots = FakeSnapshots()
    driver = AndroidUiDriver(adb, _registry(), {"app": AppInstance("app", 2, "sf-2")}, tmp_path, settle_seconds=0, snapshots=snapshots)
    observation = driver.observe("app")
    assert "Save" in observation.visible_context
    driver.act("app", {"action": "click", "element_id": 0})
    assert ("screenshot", "sf-2") in adb.calls
    assert snapshots.calls == [(2, "test.app")]
    assert ("tap", 2, 60, 40) in adb.calls
    with pytest.raises(Exception, match="element_id 99"):
        driver.act("app", {"action": "click", "element_id": 99})


def test_android_driver_remaps_to_actual_primary_observation_surface(tmp_path: Path) -> None:
    adb = FakeAdb()
    adb.actual_displays = {"test.app": [0]}
    snapshots = FakeSnapshots()
    driver = AndroidUiDriver(adb, _registry(), {"app": AppInstance("app", 2, "sf-2")}, tmp_path, settle_seconds=0, snapshots=snapshots)
    driver.observe("app")
    driver.act("app", {"action": "click", "element_id": 0})
    assert ("screenshot-primary",) in adb.calls
    assert snapshots.calls == [(0, "test.app")]
    assert ("tap", 0, 60, 40) in adb.calls
    assert driver.surface_events()[0]["display_remapped"] is True
