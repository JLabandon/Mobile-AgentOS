from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageStat

from ..adb import AdbClient, AndroidDisplayInfo
from ..benchmark.loaders import load_app_configs
from ..report import RunReporter
from ..visualization.timeline import write_timeline
from ..vlm import GeminiScreenClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ProbeAgent:
    name: str
    app_name: str
    task_context: str
    requested_display_id: int
    actual_display_id: int
    surfaceflinger_id: str | None
    package_name: str
    app_label: str


def _display_by_id(adb: AdbClient) -> dict[int, AndroidDisplayInfo]:
    return {display.display_id: display for display in adb.list_displays()}


def _screenshot_is_informative(path: Path) -> tuple[bool, str]:
    image = Image.open(path).convert("RGB")
    stat = ImageStat.Stat(image)
    extrema = image.getextrema()
    dynamic_range = max(high - low for low, high in extrema)
    mean = sum(stat.mean) / len(stat.mean)
    ok = dynamic_range > 12 and 5 < mean < 250
    return ok, f"size={image.size}, dynamic_range={dynamic_range:.1f}, mean={mean:.1f}"


def _capture_display(adb: AdbClient, *, display_id: int, surfaceflinger_id: str | None, out_path: Path) -> Path:
    if display_id == 0:
        return adb.screenshot(out_path)
    if surfaceflinger_id:
        return adb.screenshot_display(surfaceflinger_id, out_path)
    return adb.screenshot_display(display_id, out_path)


def _launch_probe_agent(
    *,
    adb: AdbClient,
    reporter: RunReporter,
    app_name: str,
    app_label: str,
    package_candidates: list[str],
    requested_display_id: int,
    task_context: str,
) -> ProbeAgent:
    package_name = adb.pick_package(package_candidates)
    adb.force_stop(package_name)
    adb.launch_package_on_display(package_name, requested_display_id)
    adb.settle(2.0)
    actual_ids = adb.package_display_ids().get(package_name, [])
    actual_display_id = requested_display_id if requested_display_id in actual_ids else (actual_ids[0] if actual_ids else requested_display_id)
    displays = _display_by_id(adb)
    surfaceflinger_id = displays.get(actual_display_id).surfaceflinger_id if actual_display_id in displays else None
    agent_name = f"{app_name}_agent"
    reporter.event(
        "display_slot_allocated",
        agent=agent_name,
        app=app_name,
        package=package_name,
        requested_display_id=requested_display_id,
        actual_display_id=actual_display_id,
        surfaceflinger_id=surfaceflinger_id,
    )
    if actual_display_id != requested_display_id:
        reporter.event(
            "observation_surface_remapped",
            agent=agent_name,
            requested_display_id=requested_display_id,
            actual_display_id=actual_display_id,
        )
    return ProbeAgent(
        name=agent_name,
        app_name=app_name,
        task_context=task_context,
        requested_display_id=requested_display_id,
        actual_display_id=actual_display_id,
        surfaceflinger_id=surfaceflinger_id,
        package_name=package_name,
        app_label=app_label,
    )


def _inspect_one(client: GeminiScreenClient, agent: ProbeAgent, screenshot_path: Path) -> str:
    return client.inspect_screen(
        screenshot_path=screenshot_path,
        agent_name=agent.name,
        app_label=agent.app_label,
        task_context=agent.task_context,
    ).text


def _observe_one(adb: AdbClient, reporter: RunReporter, run_dir: Path, agent: ProbeAgent) -> Path:
    step_dir = run_dir / agent.name
    step_dir.mkdir(parents=True, exist_ok=True)
    reporter.state_event(agent.name, "OBSERVING", display_id=agent.actual_display_id, requested_display_id=agent.requested_display_id)
    screenshot_path = _capture_display(
        adb,
        display_id=agent.actual_display_id,
        surfaceflinger_id=agent.surfaceflinger_id,
        out_path=step_dir / "observation.png",
    )
    ok, detail = _screenshot_is_informative(screenshot_path)
    reporter.event(
        "display_screenshot_captured",
        agent=agent.name,
        display_id=agent.actual_display_id,
        requested_display_id=agent.requested_display_id,
        screenshot=str(screenshot_path),
        informative=ok,
        validation=detail,
    )
    if not ok:
        raise RuntimeError(f"{agent.name} screenshot is not informative: {detail}")
    return screenshot_path


def run_probe(*, task: str, apps: tuple[str, str], run_root: Path, mode: str) -> Path:
    adb = AdbClient()
    adb.require_device()
    configs = load_app_configs(PROJECT_ROOT / "config" / "apps.json")
    displays = adb.list_displays()
    virtual_slots = [display.display_id for display in displays if display.display_id != 0 and display.can_host_tasks]
    if not virtual_slots:
        raise RuntimeError("no task-hosting virtual display found; create helper VirtualDisplays before running this probe")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / f"{task}_{mode}_display_overlap_{timestamp}"
    reporter = RunReporter(run_dir)
    reporter.event("runtime_start", runtime=mode, task=task, mode="display_overlap_probe")

    requested_displays = virtual_slots[:2] if len(virtual_slots) >= 2 else [0, virtual_slots[0]]
    probe_agents: list[ProbeAgent] = []
    contexts = {
        apps[0]: "Requester side of a cross-app task. It may need information from a peer app before continuing.",
        apps[1]: "Provider side of a cross-app task. It may contain information that can be supplied to a peer app.",
    }
    for index, app_name in enumerate(apps):
        config = configs[app_name]
        requested = requested_displays[min(index, len(requested_displays) - 1)]
        probe_agents.append(
            _launch_probe_agent(
                adb=adb,
                reporter=reporter,
                app_name=app_name,
                app_label=config.label,
                package_candidates=config.package_candidates,
                requested_display_id=requested,
                task_context=contexts[app_name],
            )
        )

    client = GeminiScreenClient()

    if mode == "steward_serial":
        for agent in probe_agents:
            screenshot_path = _observe_one(adb, reporter, run_dir, agent)
            reporter.state_event(agent.name, "THINKING", display_id=agent.actual_display_id, requested_display_id=agent.requested_display_id)
            reporter.event(
                "llm_submitted",
                agent=agent.name,
                model=client.model,
                display_id=agent.actual_display_id,
                requested_display_id=agent.requested_display_id,
                screenshot=str(screenshot_path),
            )
            response = _inspect_one(client, agent, screenshot_path)
            (run_dir / agent.name / "vlm_response.txt").write_text(response + "\n", encoding="utf-8")
            reporter.event("model_call", agent=agent.name, step=1, attempt=1, prompt="<screenshot observation>", response=response)
            reporter.event("llm_completed", agent=agent.name, model=client.model, display_id=agent.actual_display_id)
            reporter.state_event(agent.name, "DONE", display_id=agent.actual_display_id, requested_display_id=agent.requested_display_id)
    else:
        screenshots = {agent.name: _observe_one(adb, reporter, run_dir, agent) for agent in probe_agents}
        futures = {}
        with ThreadPoolExecutor(max_workers=len(probe_agents)) as executor:
            for agent in probe_agents:
                reporter.state_event(agent.name, "THINKING", display_id=agent.actual_display_id, requested_display_id=agent.requested_display_id)
                reporter.event(
                    "llm_submitted",
                    agent=agent.name,
                    model=client.model,
                    display_id=agent.actual_display_id,
                    requested_display_id=agent.requested_display_id,
                    screenshot=str(screenshots[agent.name]),
                )
                futures[executor.submit(_inspect_one, client, agent, screenshots[agent.name])] = agent
            for future in as_completed(futures):
                agent = futures[future]
                try:
                    response = future.result()
                    (run_dir / agent.name / "vlm_response.txt").write_text(response + "\n", encoding="utf-8")
                    reporter.event("model_call", agent=agent.name, step=1, attempt=1, prompt="<screenshot observation>", response=response)
                    reporter.event("llm_completed", agent=agent.name, model=client.model, display_id=agent.actual_display_id)
                    reporter.state_event(agent.name, "DONE", display_id=agent.actual_display_id, requested_display_id=agent.requested_display_id)
                except Exception as exc:
                    reporter.event("error", agent=agent.name, message=f"{exc.__class__.__name__}: {exc}")
                    reporter.state_event(agent.name, "FAILED", display_id=agent.actual_display_id, requested_display_id=agent.requested_display_id)
                    raise

    reporter.event("runtime_finish", runtime=mode, task=task, success=True, mode="display_overlap_probe")
    summary = reporter.write_summary(task=task, runtime=mode, success=True)
    timeline = write_timeline(run_root, [run_dir])
    (run_dir / "overlap_probe.json").write_text(
        json.dumps(
            {
                "summary": str(summary),
                "timeline": str(timeline),
                "agents": [agent.__dict__ for agent in probe_agents],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an AgentOS multi-display VLM overlap probe.")
    parser.add_argument("--task", default="calendar_keep_info")
    parser.add_argument("--apps", default="calendar,keep")
    parser.add_argument("--run-root", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--gemini-model", default="")
    parser.add_argument("--mode", choices=["steward_serial", "agentos_parallel"], default="agentos_parallel")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.gemini_model:
        os.environ["GEMINI_MODEL"] = args.gemini_model
    apps = tuple(item.strip() for item in args.apps.split(",") if item.strip())
    if len(apps) != 2:
        raise SystemExit("--apps must contain exactly two comma-separated app names")
    run_dir = run_probe(task=args.task, apps=(apps[0], apps[1]), run_root=Path(args.run_root), mode=args.mode)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
