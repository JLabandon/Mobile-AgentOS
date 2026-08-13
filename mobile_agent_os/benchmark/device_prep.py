from __future__ import annotations

from ..adb import AdbClient
from ..agents import AppConfig
from ..report import RunReporter


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
    proc = adb.shell(
        "content",
        "delete",
        "--uri",
        "content://com.android.calendar/events",
        "--where",
        "1=1",
        timeout=20,
    )
    reporter.event(
        "preflight_calendar_events_clear",
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
