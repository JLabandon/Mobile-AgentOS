from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agents import AppConfig
from ..steward import StewardAgent
from ..llm import DeepSeekClient
from ..report import RunReporter
from .environment import load_env_file
from .loaders import load_app_configs, load_task_plans


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PlannerOnlyAgent:
    def __init__(self, config: AppConfig, llm: DeepSeekClient) -> None:
        self.config = config
        self.llm = llm
        self.available_peers: list[AppConfig] = []


def run_planner_sanity(*, suite_path: Path, apps_path: Path, out_dir: Path, modes: tuple[str, ...], tasks: tuple[str, ...] = ()) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    reporter = RunReporter(out_dir)
    configs = load_app_configs(apps_path)
    llm = DeepSeekClient()
    rows: list[dict[str, Any]] = []
    for mode in modes:
        plans = load_task_plans(suite_path, runtime=mode)
        if tasks:
            plans = {task_id: plan for task_id, plan in plans.items() if task_id in tasks}
        agents = {name: PlannerOnlyAgent(config, llm) for name, config in configs.items()}
        steward = StewardAgent(agents, reporter, plans, mode=mode)  # type: ignore[arg-type]
        for task_id, task in plans.items():
            plan = steward.plan(task_id)
            prompt_text = _steward_prompt(out_dir, mode, task_id)
            hidden_tokens = _hidden_tokens(task.success_criteria, task.environment)
            leaked = sorted(
                token
                for token in hidden_tokens
                if token and token in prompt_text and token not in task.goal
            )
            rows.append(
                {
                    "mode": mode,
                    "task": task_id,
                    "goal": task.goal,
                    "subtasks": [
                        {
                            "agent_name": subtask.agent_name,
                            "instruction": subtask.instruction,
                            "max_steps": subtask.max_steps,
                            "expected_visible_terms": list(subtask.required_terms),
                            "not_visible_at_finish": list(subtask.forbidden_terms),
                        }
                        for subtask in plan.subtasks
                    ],
                    "edges": [list(edge) for edge in plan.edges],
                    "hidden_prompt_leaks": leaked,
                }
            )
    report_path = out_dir / "planner_sanity_report.md"
    report_path.write_text(_render_report(rows), encoding="utf-8")
    (out_dir / "planner_sanity.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report_path


def _hidden_tokens(success_criteria: dict[str, Any], environment: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in success_criteria.get("visible_terms", []):
        values.add(str(item))
    for item in success_criteria.get("not_visible_at_finish", []):
        values.add(str(item))
    for value in environment.values():
        if isinstance(value, list):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return {value for value in values if len(value) >= 4}


def _steward_prompt(out_dir: Path, mode: str, task_id: str) -> str:
    prompt = out_dir / "steward_agent" / "plan" / mode / task_id / "llm_prompt.json"
    if not prompt.exists():
        return ""
    return prompt.read_text(encoding="utf-8")


def _render_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Mobile AgentOS Planner Sanity Report",
        "",
        "This report only runs the Steward planner. It does not launch emulator apps.",
        "",
        "## Summary",
        "",
    ]
    leaks = [row for row in rows if row["hidden_prompt_leaks"]]
    lines.append(f"- Planned tasks: `{len(rows)}`")
    lines.append(f"- Hidden oracle prompt leaks: `{len(leaks)}`")
    lines.extend(["", "## Plans", ""])
    for row in rows:
        lines.append(f"### {row['task']} / {row['mode']}")
        lines.append("")
        lines.append(f"- Goal: {row['goal']}")
        lines.append(f"- Edges: `{row['edges']}`")
        lines.append(f"- Hidden leaks: `{row['hidden_prompt_leaks']}`")
        lines.append("- Subtasks:")
        for subtask in row["subtasks"]:
            lines.append(f"  - `{subtask['agent_name']}`: {subtask['instruction']}")
        lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Steward planner sanity checks without emulator.")
    parser.add_argument("--task-suite", default="curated_core")
    parser.add_argument("--apps-config", default=str(PROJECT_ROOT / "config" / "apps.json"))
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "runs" / f"planner_sanity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
    parser.add_argument("--modes", default="steward_serial,async_single_display")
    parser.add_argument("--tasks", help="Comma-separated task ids. Defaults to the whole suite.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    load_env_file(PROJECT_ROOT.parent / "agent_ipc_mvp" / ".env")
    args = parse_args(argv)
    suite_path = PROJECT_ROOT / "benchmarks" / "tasks" / f"{args.task_suite}.json"
    modes = tuple(item.strip() for item in args.modes.split(",") if item.strip())
    tasks = tuple(item.strip() for item in (args.tasks or "").split(",") if item.strip())
    report_path = run_planner_sanity(
        suite_path=suite_path,
        apps_path=Path(args.apps_config),
        out_dir=Path(args.out_dir),
        modes=modes,
        tasks=tasks,
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
