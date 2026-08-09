from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .adb import AdbClient
from .agents import AppConfig, CalendarAgent, ClockAgent, StewardAgent
from .llm import DeepSeekClient
from .report import RunReporter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_app_configs(path: Path) -> dict[str, AppConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: AppConfig(
            name=name,
            label=value["label"],
            package_candidates=list(value["package_candidates"]),
            launch=dict(value["launch"]),
        )
        for name, value in raw.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent IPC MVP centralized Steward demo.")
    parser.add_argument("--task", default="calendar_clock_draft", choices=["calendar_clock_draft"])
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "apps.json"))
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
            smoke = llm.smoke_test()
            reporter.event("environment", message=f"DeepSeek smoke: {smoke}")

        configs = load_app_configs(Path(args.config))
        agents = {
            "calendar": CalendarAgent(config=configs["calendar"], adb=adb, llm=llm, reporter=reporter),
            "clock": ClockAgent(config=configs["clock"], adb=adb, llm=llm, reporter=reporter),
        }
        steward = StewardAgent(agents, reporter)
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
