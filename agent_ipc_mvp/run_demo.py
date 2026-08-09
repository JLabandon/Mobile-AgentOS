from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .adb import AdbClient
from .agents import AppConfig, AppStaffAgent, StewardAgent, SubTask
from .llm import DeepSeekClient
from .registry import AgentRegistry
from .report import RunReporter
from .task_plan import TaskPlan


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_app_configs(path: Path) -> dict[str, AppConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
            name: AppConfig(
                name=name,
                label=value["label"],
                package_candidates=list(value["package_candidates"]),
                launch=dict(value["launch"]),
                capabilities=tuple(value.get("capabilities", [])),
                description=str(value.get("description", "")),
                task_guidelines=tuple(value.get("task_guidelines", [])),
                semantic_slots=dict(value.get("semantic_slots", {})),
            )
        for name, value in raw.items()
    }


def load_task_plans(path: Path, mode: str) -> dict[str, TaskPlan]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    plans: dict[str, TaskPlan] = {}
    for task_id, value in raw.items():
        subtasks = tuple(
            SubTask(
                agent_name=str(item["agent_name"]),
                instruction=str(item["instruction"]),
                max_steps=int(item.get("max_steps", 6)),
                required_terms=tuple(item.get("required_terms", [])),
                forbidden_terms=tuple(item.get("forbidden_terms", [])),
                launch_args=tuple(item.get("launch_args", [])),
            )
            for item in value.get("subtasks", [])
        )
        plans[task_id] = TaskPlan(
            task_id=task_id,
            goal=str(value["goal"]),
            subtasks=subtasks,
            edges=tuple(tuple(edge) for edge in value.get("edges", [])),
            mode=mode,
        )
    return plans


def run_deepseek_smoke(llm: DeepSeekClient, reporter: RunReporter, run_dir: Path) -> None:
    system = (
        "Return json only. Example json output: {\"ok\": true}. "
        "Do not include markdown or explanation."
    )
    user = 'Return exactly this json object: {"ok": true}'
    prompt_path = run_dir / "deepseek_smoke_prompt.json"
    response_path = run_dir / "deepseek_smoke_response.txt"
    prompt_path.write_text(
        json.dumps({"system": system, "user": user}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raw_content = llm.raw_chat(system=system, user=user, max_tokens=50)
    response_path.write_text(raw_content, encoding="utf-8", errors="replace")
    reporter.event(
        "model_call",
        agent="deepseek_smoke",
        step=0,
        attempt=1,
        prompt=str(prompt_path),
        response=str(response_path),
        raw_response=raw_content,
    )
    smoke = llm.parse_json_content(raw_content)
    reporter.event("environment", message=f"DeepSeek smoke: {smoke}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent IPC MVP demo.")
    parser.add_argument("--task", default="calendar_clock")
    parser.add_argument("--mode", default="steward", choices=["steward", "peer"])
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "apps.json"))
    parser.add_argument("--tasks-config", default=str(PROJECT_ROOT / "config" / "tasks.json"))
    parser.add_argument("--runs-dir", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--device", help="Optional adb serial. Defaults to ANDROID_SERIAL or first online device.")
    parser.add_argument("--skip-api-smoke", action="store_true", help="Skip DeepSeek smoke test.")
    args = parser.parse_args(argv)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.runs_dir).resolve() / f"{args.task}_{timestamp}"
    reporter = RunReporter(run_dir)
    success = False
    run_error: str | None = None
    try:
        adb = AdbClient(device=args.device)
        device = adb.require_device()
        reporter.event("environment", message=f"adb device: {device}")

        llm = DeepSeekClient()
        if not args.skip_api_smoke:
            run_deepseek_smoke(llm, reporter, run_dir)

        configs = load_app_configs(Path(args.config))
        task_plans = load_task_plans(Path(args.tasks_config), mode=args.mode)
        agents = {
            name: AppStaffAgent(config=config, adb=adb, llm=llm, reporter=reporter)
            for name, config in configs.items()
        }
        registry = AgentRegistry(agents, configs)
        reporter.event("agent_registry", registry=registry.trace_payload())
        steward = StewardAgent(agents, reporter, task_plans=task_plans, mode=args.mode)
        success = steward.run(args.task, run_dir)
    except Exception as exc:
        run_error = str(exc)
        reporter.event("error", message=run_error)
        success = False
    finally:
        summary_path = reporter.write_summary(task=args.task, success=success, run_error=run_error)
        print(summary_path)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
