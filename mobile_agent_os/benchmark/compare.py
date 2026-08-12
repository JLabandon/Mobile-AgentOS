from __future__ import annotations

import json
from pathlib import Path
from typing import Any


METRIC_KEYS = [
    "success",
    "wall_clock_time",
    "llm_calls",
    "adb_ui_actions",
    "steward_turns",
    "ipc_messages",
    "wait_peer_time",
    "app_switches",
    "total_runtime_events",
]


def write_comparison(run_root: Path, metrics: list[dict[str, Any]]) -> Path:
    path = run_root / "comparison.md"
    lines = [
        "# Mobile AgentOS Benchmark Comparison",
        "",
        "| task | runtime | success | wall-clock time | LLM calls | ADB/UI actions | Steward turns | IPC messages | WAIT_PEER time | app switches | total events |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in metrics:
        lines.append(
            "| {task} | {runtime} | {success} | {wall_clock_time} | {llm_calls} | {adb_ui_actions} | {steward_turns} | {ipc_messages} | {wait_peer_time} | {app_switches} | {total_runtime_events} |".format(
                **item
            )
        )
    lines.extend(["", "## Raw Metrics", "", "```json", json.dumps(metrics, indent=2, ensure_ascii=False), "```"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
