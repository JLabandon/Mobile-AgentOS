from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..adb import AdbClient, AdbError, AndroidDisplayInfo
from ..benchmark.environment import load_env_file
from ..benchmark.loaders import load_app_configs


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Android multidisplay support for Mobile AgentOS Stage 3.")
    parser.add_argument("--apps", default="keep,gmail,calendar", help="Comma-separated app names from config/apps.json.")
    parser.add_argument("--apps-config", default=str(PROJECT_ROOT / "config" / "apps.json"))
    parser.add_argument("--runs-dir", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--device", help="Optional adb serial. Defaults to ANDROID_SERIAL or first online device.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    args = parse_args(argv)
    run_dir = Path(args.runs_dir).resolve() / f"multidisplay_feasibility_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    adb = AdbClient(device=args.device)
    result = run_probe(adb=adb, app_names=[item.strip() for item in args.apps.split(",") if item.strip()], apps_path=Path(args.apps_config), run_dir=run_dir)
    report = write_report(run_dir, result)
    print(report)
    return 0 if result["summary"]["stage3_feasible"] else 1


def run_probe(*, adb: AdbClient, app_names: list[str], apps_path: Path, run_dir: Path) -> dict[str, Any]:
    device = adb.require_device()
    configs = load_app_configs(apps_path)
    displays = adb.list_displays()
    display_slots = [
        display
        for display in displays
        if display.can_host_tasks or (display.kind == "virtual" and display.surfaceflinger_id)
    ]
    preferred_slots = [display for display in display_slots if display.display_id != 0] or display_slots
    trace: list[dict[str, Any]] = []
    launched: list[dict[str, Any]] = []
    screenshots: list[dict[str, Any]] = []
    input_results: list[dict[str, Any]] = []

    for idx, app_name in enumerate(app_names):
        if app_name not in configs or idx >= len(preferred_slots):
            continue
        config = configs[app_name]
        package = adb.pick_package(config.package_candidates)
        slot = preferred_slots[idx]
        event: dict[str, Any] = {"app": app_name, "package": package, "display_id": slot.display_id}
        try:
            adb.force_stop(package)
            proc = adb.launch_package_on_display(package, slot.display_id)
            event.update({"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
        except Exception as exc:
            event.update({"ok": False, "error": str(exc)})
        launched.append(event)
        trace.append({"kind": "launch_on_display", **event})

    after_launch = adb.list_displays()
    for display in after_launch:
        if not display.can_host_tasks:
            continue
        input_event: dict[str, Any] = {"display_id": display.display_id}
        try:
            proc = adb.tap_display(display.display_id, 8, 8)
            input_event.update({"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
        except Exception as exc:
            input_event.update({"ok": False, "error": str(exc)})
        input_results.append(input_event)
        trace.append({"kind": "tap_display", **input_event})

        screenshot_event: dict[str, Any] = {"display_id": display.display_id, "surfaceflinger_id": display.surfaceflinger_id}
        if display.surfaceflinger_id:
            out_path = run_dir / "screenshots" / f"display_{display.display_id}.png"
            try:
                adb.screenshot_display(display.surfaceflinger_id, out_path)
                screenshot_event.update({"ok": out_path.exists() and out_path.stat().st_size > 0, "path": str(out_path), "bytes": out_path.stat().st_size})
            except Exception as exc:
                screenshot_event.update({"ok": False, "error": str(exc)})
        else:
            screenshot_event.update({"ok": False, "error": "missing SurfaceFlinger display id"})
        screenshots.append(screenshot_event)
        trace.append({"kind": "screenshot_display", **screenshot_event})

    final_displays = adb.list_displays()
    visible_launched = [
        event
        for event in launched
        if any(
            display.display_id == event["display_id"] and display.top_activity.startswith(f"{event['package']}/")
            for display in final_displays
        )
    ]
    summary = {
        "device": device,
        "display_count": len(displays),
        "host_task_display_count": len(display_slots),
        "virtual_host_task_display_count": len([display for display in display_slots if display.display_id != 0]),
        "launch_on_display_success_count": len(visible_launched),
        "tap_display_success_count": len([event for event in input_results if event.get("ok")]),
        "screenshot_success_count": len([event for event in screenshots if event.get("ok")]),
        "stage3_feasible": len(visible_launched) >= 2 and len([event for event in input_results if event.get("ok")]) >= 2,
    }
    result = {
        "summary": summary,
        "initial_displays": [display_to_json(display) for display in displays],
        "final_displays": [display_to_json(display) for display in final_displays],
        "launches": launched,
        "input_results": input_results,
        "screenshots": screenshots,
        "trace": trace,
    }
    (run_dir / "display_slots.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "display_trace.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in trace) + "\n", encoding="utf-8")
    return result


def display_to_json(display: AndroidDisplayInfo) -> dict[str, Any]:
    return {
        "display_id": display.display_id,
        "width": display.width,
        "height": display.height,
        "kind": display.kind,
        "name": display.name,
        "unique_id": display.unique_id,
        "can_host_tasks": display.can_host_tasks,
        "has_content": display.has_content,
        "top_activity": display.top_activity,
        "surfaceflinger_id": display.surfaceflinger_id,
    }


def write_report(run_dir: Path, result: dict[str, Any]) -> Path:
    summary = result["summary"]
    lines = [
        "# Multidisplay Feasibility Report",
        "",
        f"- Device: `{summary['device']}`",
        f"- Displays discovered: `{summary['display_count']}`",
        f"- Task-hosting displays: `{summary['host_task_display_count']}`",
        f"- Virtual task-hosting displays: `{summary['virtual_host_task_display_count']}`",
        f"- Launch-on-display successes: `{summary['launch_on_display_success_count']}`",
        f"- Display-targeted tap successes: `{summary['tap_display_success_count']}`",
        f"- Display screenshot successes: `{summary['screenshot_success_count']}`",
        f"- Stage3 feasible: `{summary['stage3_feasible']}`",
        "",
        "## Final Displays",
        "",
        "| display | kind | size | surfaceflinger | top activity |",
        "|---:|---|---|---|---|",
    ]
    for display in result["final_displays"]:
        lines.append(
            f"| {display['display_id']} | {display['kind']} | {display['width']}x{display['height']} | {display['surfaceflinger_id'] or ''} | {display['top_activity']} |"
        )
    lines.extend(["", "## Evidence Files", "", f"- `{run_dir / 'display_slots.json'}`", f"- `{run_dir / 'display_trace.jsonl'}`"])
    path = run_dir / "multidisplay_feasibility.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AdbError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
