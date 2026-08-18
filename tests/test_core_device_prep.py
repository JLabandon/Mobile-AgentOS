from __future__ import annotations
from pathlib import Path

from mobile_agent_os.app_agents import AppConfig
from mobile_agent_os.benchmark.device_prep import prepare_device
from mobile_agent_os.report import RunReporter


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeAdb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def require_device(self) -> str:
        return "emulator-5554"

    def shell(self, *args: str, timeout: int = 30, check: bool = False) -> FakeProc:  # noqa: ARG002
        self.calls.append(("shell", args))
        if args[:4] == ("content", "query", "--uri", "content://com.android.calendar/calendars"):
            return FakeProc(stdout="Row: 0 _id=2, calendar_displayName=Primary, calendar_access_level=700, visible=1\n")
        if args[:4] == ("content", "query", "--uri", "content://com.android.calendar/events"):
            where = args[args.index("--where") + 1].strip("'") if "--where" in args else ""
            if where == "calendar_id=2 AND deleted=0" and not any(
                call == ("shell", ("content", "delete", "--uri", "content://com.android.calendar/events", "--where", "'calendar_id=2'"))
                for call in self.calls
            ):
                return FakeProc(stdout="Row: 0 _id=10, title=Old Event, calendar_id=2, deleted=0\n")
            return FakeProc(stdout="No result found.\n")
        return FakeProc(stdout="ok")

    def pick_package(self, candidates: list[str]) -> str:
        self.calls.append(("pick_package", tuple(candidates)))
        return candidates[0]

    def clear_app_data(self, package_name: str) -> FakeProc:
        self.calls.append(("clear_app_data", (package_name,)))
        return FakeProc(stdout="Success")

    def package_exists(self, package_name: str) -> bool:
        self.calls.append(("package_exists", (package_name,)))
        return True

    def force_stop(self, package_name: str) -> FakeProc:
        self.calls.append(("force_stop", (package_name,)))
        return FakeProc()


def test_prepare_device_applies_generic_preflight_and_clock_reset(tmp_path: Path) -> None:
    adb = FakeAdb()
    reporter = RunReporter(tmp_path)
    configs = {
        "clock": AppConfig(
            name="clock",
            label="Clock",
            package_candidates=["com.google.android.deskclock"],
            launch={},
        ),
        "calendar": AppConfig(
            name="calendar",
            label="Calendar",
            package_candidates=["com.google.android.calendar"],
            launch={},
        ),
    }

    prepare_device(adb, configs, reporter)  # type: ignore[arg-type]

    assert ("shell", ("settings", "put", "global", "window_animation_scale", "0")) in adb.calls
    assert (
        "shell",
        ("content", "delete", "--uri", "content://com.android.calendar/events", "--where", "'calendar_id=2'"),
    ) in adb.calls
    assert ("clear_app_data", ("com.google.android.deskclock",)) in adb.calls
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "preflight_calendar_events_clear" in trace
    assert '"verified": true' in trace
    assert "preflight_clock_alarms_clear" in trace
