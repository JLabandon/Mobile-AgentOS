from __future__ import annotations

import shlex
from pathlib import Path

from mobile_agent_os.agents import AppConfig
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
        self.remaining_titles = {
            "Agent IPC MVP Meeting": 2,
            "Investor Check-in": 1,
            "Offsite Visit": 1,
            "Product Sync": 1,
            "SEA Design Review Flight": 1,
        }

    def require_device(self) -> str:
        return "emulator-5554"

    def shell(self, *args: str, timeout: int = 30, check: bool = False) -> FakeProc:  # noqa: ARG002
        self.calls.append(("shell", args))
        if len(args) == 1 and args[0].startswith("content query --uri content://com.android.calendar/events"):
            parts = shlex.split(args[0])
            where = parts[parts.index("--where") + 1]
            title = self._title_from_where(where)
            count = self.remaining_titles.get(title, 0)
            if count == 0:
                return FakeProc(stdout="No result found.")
            return FakeProc(stdout="\n".join(f"Row: {i} _id={i}, title={title}" for i in range(count)))
        if len(args) == 1 and args[0].startswith("content delete --uri content://com.android.calendar/events"):
            parts = shlex.split(args[0])
            where = parts[parts.index("--where") + 1]
            title = self._title_from_where(where)
            self.remaining_titles[title] = 0
            return FakeProc(stdout="")
        return FakeProc(stdout="ok")

    def _title_from_where(self, where: str) -> str:
        value = where.split("=", 1)[1]
        value = value.split(" AND ", 1)[0]
        return value.strip("'").replace("''", "'")

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


def test_prepare_device_clears_calendar_events_and_clock_alarms(tmp_path: Path) -> None:
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

    assert (
        "shell",
        ("content delete --uri content://com.android.calendar/events --where 'title='\"'\"'Agent IPC MVP Meeting'\"'\"' AND deleted=0'",),
    ) in adb.calls
    assert ("clear_app_data", ("com.google.android.deskclock",)) in adb.calls
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "preflight_calendar_benchmark_events_clear" in trace
    assert '"after_count": 0' in trace
    assert "preflight_clock_alarms_clear" in trace
