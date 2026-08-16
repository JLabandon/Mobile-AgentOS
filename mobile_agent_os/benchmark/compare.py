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
    "switch_time",
    "total_runtime_events",
    "llm_thinking_time",
    "llm_overlap_time",
    "resource_wait_time",
    "fast_guard_pass_count",
    "fast_guard_fail_count",
    "useful_parallel_progress_ratio",
    "parallel_thinking_overlap_time",
    "observe_overlap_time",
    "action_overlap_time",
    "settle_overlap_time",
    "cross_stage_overlap_time",
    "observation_jobs",
    "thinking_jobs",
    "action_jobs",
    "settle_jobs",
    "ipc_delivery_jobs",
]


def write_comparison(run_root: Path, metrics: list[dict[str, Any]]) -> Path:
    path = run_root / "comparison.md"
    lines = [
        "# Mobile AgentOS Benchmark Comparison",
        "",
        "| task | runtime | success | wall-clock | LLM calls | UI actions | steward turns | IPC | WAIT_PEER | app switches | switch time | events | thinking | overlap | resource wait | guard pass | guard fail | parallel ratio | thinking overlap | observe overlap | action overlap | settle overlap | cross-stage overlap | observe jobs | think jobs | action jobs | settle jobs | IPC jobs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in metrics:
        lines.append(
            "| {task} | {runtime} | {success} | {wall_clock_time} | {llm_calls} | {adb_ui_actions} | {steward_turns} | {ipc_messages} | {wait_peer_time} | {app_switches} | {switch_time} | {total_runtime_events} | {llm_thinking_time} | {llm_overlap_time} | {resource_wait_time} | {fast_guard_pass_count} | {fast_guard_fail_count} | {useful_parallel_progress_ratio} | {parallel_thinking_overlap_time} | {observe_overlap_time} | {action_overlap_time} | {settle_overlap_time} | {cross_stage_overlap_time} | {observation_jobs} | {thinking_jobs} | {action_jobs} | {settle_jobs} | {ipc_delivery_jobs} |".format(
                **{key: item.get(key, "") for key in ["task", "runtime", *METRIC_KEYS]}
            )
        )
    lines.extend(["", "## Raw Metrics", "", "```json", json.dumps(metrics, indent=2, ensure_ascii=False), "```"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
