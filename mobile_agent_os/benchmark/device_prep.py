from __future__ import annotations

import shlex

from ..adb import AdbClient
from ..agents import AppConfig
from ..report import RunReporter


CALENDAR_EVENTS_URI = "content://com.android.calendar/events"
BENCHMARK_CALENDAR_TITLES = (
    "Agent IPC MVP Meeting",
    "Investor Check-in",
    "Offsite Visit",
    "Product Sync",
    "SEA Design Review Flight",
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _calendar_title_count(adb: AdbClient, title: str) -> tuple[int, str, str]:
    where = f"title={_sql_literal(title)} AND deleted=0"
    command = (
        f"content query --uri {shlex.quote(CALENDAR_EVENTS_URI)} "
        f"--projection _id:title --where {shlex.quote(where)}"
    )
    proc = adb.shell(command, timeout=20)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if "No result found" in stdout:
        return 0, stdout, stderr
    return stdout.count("Row:"), stdout, stderr


def clear_calendar_records(adb: AdbClient, reporter: RunReporter) -> None:
    results = []
    for title in BENCHMARK_CALENDAR_TITLES:
        where = f"title={_sql_literal(title)} AND deleted=0"
        before_count, before_stdout, before_stderr = _calendar_title_count(adb, title)
        command = f"content delete --uri {shlex.quote(CALENDAR_EVENTS_URI)} --where {shlex.quote(where)}"
        proc = adb.shell(command, timeout=20)
        after_count, after_stdout, after_stderr = _calendar_title_count(adb, title)
        stderr = proc.stderr.strip()
        results.append(
            {
                "title": title,
                "where": where,
                "before_count": before_count,
                "after_count": after_count,
                "ok": proc.returncode == 0 and "Error while accessing provider" not in stderr and after_count == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": stderr,
                "before_query": {"stdout": before_stdout[:500], "stderr": before_stderr[:500]},
                "after_query": {"stdout": after_stdout[:500], "stderr": after_stderr[:500]},
            }
        )
    reporter.event(
        "preflight_calendar_benchmark_events_clear",
        uri=CALENDAR_EVENTS_URI,
        titles=list(BENCHMARK_CALENDAR_TITLES),
        ok=all(item["ok"] for item in results),
        results=results,
    )


def clear_clock_alarms(adb: AdbClient, configs: dict[str, AppConfig], reporter: RunReporter) -> None:
    config = configs.get("clock")
    if not config:
        reporter.event("preflight_clock_alarms_clear_skip", reason="clock app is not configured")
        return
    try:
        package_name = adb.pick_package(config.package_candidates)
    except Exception as exc:
        reporter.event("preflight_clock_alarms_clear_skip", reason=str(exc))
        return
    proc = adb.clear_app_data(package_name)
    reporter.event(
        "preflight_clock_alarms_clear",
        package=package_name,
        returncode=proc.returncode,
        stdout=proc.stdout.strip(),
        stderr=proc.stderr.strip(),
    )


def prepare_device(adb: AdbClient, configs: dict[str, AppConfig], reporter: RunReporter) -> str:
    detected = adb.require_device()
    reporter.event("environment", message=f"adb device: {detected}")
    for key in ["window_animation_scale", "transition_animation_scale", "animator_duration_scale"]:
        adb.shell("settings", "put", "global", key, "0", timeout=10)
        reporter.event("preflight_setting", name=key, value="0")
    clear_calendar_records(adb, reporter)
    clear_clock_alarms(adb, configs, reporter)
    for config in configs.values():
        for package_name in config.package_candidates:
            if adb.package_exists(package_name):
                adb.force_stop(package_name)
                reporter.event("preflight_app_stop", package=package_name)
    return detected


def reset_configured_app_data(
    *,
    adb: AdbClient,
    configs: dict[str, AppConfig],
    app_names: object,
    reporter: RunReporter,
) -> None:
    if not isinstance(app_names, list):
        return
    for app_name in app_names:
        config = configs.get(str(app_name))
        if not config:
            reporter.event("preflight_reset_skip", app=str(app_name), reason="unknown app")
            continue
        package_name = adb.pick_package(config.package_candidates)
        adb.clear_app_data(package_name)
        reporter.event("preflight_app_data_clear", app=str(app_name), package=package_name)
