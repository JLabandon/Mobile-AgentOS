from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from ..adb import AdbClient
from ..agents import AppStaffAgent
from ..display import AndroidDisplayManager, ForegroundObservationDisplayManager
from ..evaluator import record_hidden_evaluation
from ..llm import DeepSeekClient
from ..registry import AgentRegistry
from ..report import RunReporter
from ..runtimes import AgentOSParallelRuntime, MobileRunAgentOSRuntime, MobileRunStewardSerialRuntime, StewardSerialRuntime
from ..visualization.timeline import write_timeline
from .compare import write_comparison
from .device_prep import prepare_device, record_provider_evidence, reset_configured_app_data
from .environment import load_env_file, run_deepseek_smoke
from .loaders import load_app_configs, load_task_plans


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CLASSES = {
    "steward_serial": StewardSerialRuntime,
    "agentos_parallel": AgentOSParallelRuntime,
    "mobilerun_steward_serial": MobileRunStewardSerialRuntime,
    "mobilerun_agentos_parallel": MobileRunAgentOSRuntime,
}


def run_once(
    *,
    runtime_name: str,
    task: str,
    suite_path: Path,
    apps_path: Path,
    run_root: Path,
    device: str | None,
    skip_api_smoke: bool,
    display_backend: str,
) -> tuple[dict[str, object], Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / f"{task}_{runtime_name}_{timestamp}"
    reporter = RunReporter(run_dir)
    success = False
    run_error: str | None = None
    try:
        adb = AdbClient(device=device)
        configs = load_app_configs(apps_path)
        prepare_device(adb, configs, reporter)

        llm = DeepSeekClient()
        if not skip_api_smoke:
            run_deepseek_smoke(llm, reporter, run_dir)

        task_plans = load_task_plans(suite_path, runtime=runtime_name, variables={"run_id": timestamp})
        plan_config = task_plans[task]
        reset_configured_app_data(
            adb=adb,
            configs=configs,
            app_names=plan_config.environment.get("reset_app_data", []),
            reporter=reporter,
        )
        agents = {
            name: AppStaffAgent(config=config, adb=adb, llm=llm, reporter=reporter)
            for name, config in configs.items()
        }
        registry = AgentRegistry(agents, configs)
        reporter.event("agent_registry", registry=registry.trace_payload())
        runtime_cls = RUNTIME_CLASSES[runtime_name]
        if runtime_name == "agentos_parallel":
            if display_backend == "android":
                android_display_manager = AndroidDisplayManager(adb)
                reporter.event(
                    "display_backend_selected",
                    runtime=runtime_name,
                    backend=display_backend,
                    displays=[slot.__dict__ for slot in android_display_manager.list_slots()],
                )
                if len({slot.display_id for slot in android_display_manager.list_slots()}) <= 1:
                    reporter.event(
                        "display_backend_fallback_note",
                        runtime=runtime_name,
                        backend=display_backend,
                        reason="only one Android display is currently available; virtual-display overlap cannot be demonstrated in this run",
                    )
                    display_manager = ForegroundObservationDisplayManager(adb)
                else:
                    display_manager = android_display_manager
            else:
                display_manager = ForegroundObservationDisplayManager(adb)
                reporter.event("display_backend_selected", runtime=runtime_name, backend=display_backend)
            runtime = runtime_cls(agents, reporter, task_plans, display_manager=display_manager)
        else:
            runtime = runtime_cls(agents, reporter, task_plans)
        runtime_success = runtime.run(task, run_dir)
        plan = runtime.last_plan or task_plans[task]
        record_provider_evidence(adb, plan, reporter)
        hidden_result = record_hidden_evaluation(plan, reporter)
        success = runtime_success and hidden_result.passed
    except Exception as exc:
        run_error = str(exc)
        reporter.event("error", message=run_error)
        success = False
    finally:
        summary_path = reporter.write_summary(task=task, runtime=runtime_name, success=success, run_error=run_error)
        print(summary_path)
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    return metrics, run_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mobile AgentOS benchmark.")
    parser.add_argument("--runtime", choices=sorted(RUNTIME_CLASSES), help="Run one runtime.")
    parser.add_argument("--runtimes", help="Comma-separated runtimes.")
    parser.add_argument("--task")
    parser.add_argument("--task-suite", default="curated_core")
    parser.add_argument("--apps-config", default=str(PROJECT_ROOT / "config" / "apps.json"))
    parser.add_argument("--runs-dir", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--device", help="Optional adb serial. Defaults to ANDROID_SERIAL or first online device.")
    parser.add_argument("--display-backend", choices=["foreground", "android"], default="foreground")
    parser.add_argument("--skip-api-smoke", action="store_true")
    parser.add_argument("--continue-on-failure", action="store_true", help="Run remaining tasks after a failed item.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    load_env_file(PROJECT_ROOT.parent / "agent_ipc_mvp" / ".env")
    args = parse_args(argv)
    runtime_names = []
    if args.runtimes:
        runtime_names = [item.strip() for item in args.runtimes.split(",") if item.strip()]
    elif args.runtime:
        runtime_names = [args.runtime]
    else:
        runtime_names = ["agentos_parallel"]
    unknown = sorted(set(runtime_names) - set(RUNTIME_CLASSES))
    if unknown:
        raise ValueError(f"unknown runtimes: {unknown}")

    suite_path = PROJECT_ROOT / "benchmarks" / "tasks" / f"{args.task_suite}.json"
    task_plans = load_task_plans(suite_path, runtime=runtime_names[0])
    task_ids = [args.task] if args.task else list(task_plans)
    run_root = Path(args.runs_dir).resolve() / f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)

    metrics: list[dict[str, object]] = []
    run_dirs: list[Path] = []
    for task_id in task_ids:
        for runtime_name in runtime_names:
            item, run_dir = run_once(
                runtime_name=runtime_name,
                task=task_id,
                suite_path=suite_path,
                apps_path=Path(args.apps_config),
                run_root=run_root,
                device=args.device,
                skip_api_smoke=args.skip_api_smoke,
                display_backend=args.display_backend,
            )
            metrics.append(item)
            run_dirs.append(run_dir)
            if not item.get("success") and not args.continue_on_failure:
                reporter_root = write_comparison(run_root, metrics)
                timeline = write_timeline(run_root, run_dirs)
                print(reporter_root)
                print(timeline)
                print(f"stopped after failure: task={task_id} runtime={runtime_name}")
                return 1

    comparison = write_comparison(run_root, metrics)
    timeline = write_timeline(run_root, run_dirs)
    print(comparison)
    print(timeline)
    return 0 if all(item.get("success") for item in metrics) else 1


if __name__ == "__main__":
    sys.exit(main())
