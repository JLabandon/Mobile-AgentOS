from __future__ import annotations

from ..adb import AdbClient
from ..agents import AppConfig
from ..report import RunReporter


def _content_rows(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("Row:")]


def _field_value(row: str, field: str) -> str | None:
    marker = f"{field}="
    if marker not in row:
        return None
    value = row.split(marker, 1)[1]
    if ", " in value:
        value = value.split(", ", 1)[0]
    return value.strip()


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


def clear_calendar_events(adb: AdbClient, configs: dict[str, AppConfig], reporter: RunReporter) -> None:
    config = configs.get("calendar")
    if not config:
        reporter.event("preflight_calendar_clear_skip", reason="calendar app is not configured")
        return
    try:
        package_name = adb.pick_package(config.package_candidates)
    except Exception as exc:
        reporter.event("preflight_calendar_clear_skip", reason=str(exc))
        return
    calendars_proc = adb.shell(
        "content",
        "query",
        "--uri",
        "content://com.android.calendar/calendars",
        "--projection",
        "_id:calendar_displayName:calendar_access_level:visible",
        "--where",
        "'calendar_access_level>=500 AND visible=1'",
        timeout=20,
    )
    calendar_rows = _content_rows(calendars_proc.stdout)
    calendar_ids = [value for row in calendar_rows if (value := _field_value(row, "_id"))]
    if not calendar_ids:
        reporter.event(
            "preflight_calendar_events_clear_skip",
            package=package_name,
            reason="no writable visible calendars found",
            query_stdout=calendars_proc.stdout.strip(),
            query_stderr=calendars_proc.stderr.strip(),
        )
        return
    before_counts: dict[str, int] = {}
    after_counts: dict[str, int] = {}
    delete_results = []
    for calendar_id in calendar_ids:
        before = adb.shell(
            "content",
            "query",
            "--uri",
            "content://com.android.calendar/events",
            "--projection",
            "_id:title:calendar_id:deleted",
            "--where",
            f"'calendar_id={calendar_id} AND deleted=0'",
            timeout=20,
        )
        before_counts[calendar_id] = len(_content_rows(before.stdout))
        delete_proc = adb.shell(
            "content",
            "delete",
            "--uri",
            "content://com.android.calendar/events",
            "--where",
            f"'calendar_id={calendar_id}'",
            timeout=20,
        )
        delete_results.append(
            {
                "calendar_id": calendar_id,
                "returncode": delete_proc.returncode,
                "stdout": delete_proc.stdout.strip(),
                "stderr": delete_proc.stderr.strip(),
            }
        )
        after = adb.shell(
            "content",
            "query",
            "--uri",
            "content://com.android.calendar/events",
            "--projection",
            "_id:title:calendar_id:deleted",
            "--where",
            f"'calendar_id={calendar_id} AND deleted=0'",
            timeout=20,
        )
        after_counts[calendar_id] = len(_content_rows(after.stdout))
    reporter.event(
        "preflight_calendar_events_clear",
        package=package_name,
        calendar_ids=calendar_ids,
        before_counts=before_counts,
        after_counts=after_counts,
        verified=all(count == 0 for count in after_counts.values()),
        delete_results=delete_results,
    )


def prepare_device(adb: AdbClient, configs: dict[str, AppConfig], reporter: RunReporter) -> str:
    detected = adb.require_device()
    reporter.event("environment", message=f"adb device: {detected}")
    for key in ["window_animation_scale", "transition_animation_scale", "animator_duration_scale"]:
        adb.shell("settings", "put", "global", key, "0", timeout=10)
        reporter.event("preflight_setting", name=key, value="0")
    clear_calendar_events(adb, configs, reporter)
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
