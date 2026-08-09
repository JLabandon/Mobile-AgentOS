from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


class RunReporter:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.run_dir / "trace.jsonl"
        self.events: list[dict[str, Any]] = []

    def event(self, kind: str, **payload: Any) -> None:
        item = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            **payload,
        }
        self.events.append(item)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")

    def write_summary(self, *, task: str, success: bool, run_error: str | None = None) -> Path:
        lines = [
            "# Agent IPC MVP Run Summary",
            "",
            f"- Task: `{task}`",
            f"- Success: `{success}`",
            f"- Run directory: `{self.run_dir}`",
        ]
        if run_error:
            lines.append(f"- Error: `{run_error}`")
        lines.extend(["", "## Trace", ""])
        for event in self.events:
            kind = event.get("kind")
            if kind == "agent_step":
                lines.append(
                    f"- `{event.get('agent')}` step `{event.get('step')}`: "
                    f"action `{event.get('action')}`, status `{event.get('status')}`"
                )
                if event.get("reason"):
                    lines.append(f"  - Reason: {event.get('reason')}")
                if event.get("screenshot"):
                    lines.append(f"  - Screenshot: `{event.get('screenshot')}`")
            elif kind in {"agent_start", "agent_finish", "steward_plan", "environment"}:
                lines.append(f"- `{kind}`: {event.get('message', event)}")
            elif kind == "app_launch":
                lines.append(
                    f"- `app_launch`: `{event.get('agent')}` expected `{event.get('expected')}`, "
                    f"foreground `{event.get('foreground')}`, matched `{event.get('matched')}`"
                )
            elif kind == "agent_registry":
                lines.append(f"- `agent_registry`: {len(event.get('registry', []))} agents")
            elif kind in {
                "task_plan_created",
                "runtime_request_created",
                "runtime_request_routed",
                "runtime_request_received",
                "runtime_response_created",
                "runtime_response_delivered",
                "agent_paused",
                "agent_resumed",
            }:
                lines.append(f"- `{kind}`: {event}")
            elif kind == "completion_check":
                lines.append(
                    f"- `completion_check`: `{event.get('agent')}` step `{event.get('step')}` "
                    f"verified `{event.get('verified')}`: {event.get('message')}"
                )
            elif kind == "model_call":
                lines.append(
                    f"- `model_call`: `{event.get('agent')}` step `{event.get('step')}` "
                    f"attempt `{event.get('attempt')}`"
                )
                lines.append(f"  - Prompt: `{event.get('prompt')}`")
                lines.append(f"  - Response: `{event.get('response')}`")
                if event.get("raw_response") == "":
                    lines.append("  - Raw response: `<empty>`")
            elif kind == "model_retry":
                lines.append(
                    f"- `model_retry`: `{event.get('agent')}` step `{event.get('step')}` "
                    f"attempt `{event.get('attempt')}`: {event.get('message')}"
                )
                if event.get("prompt"):
                    lines.append(f"  - Prompt: `{event.get('prompt')}`")
            elif kind == "error":
                lines.append(f"- `error`: {event.get('message')}")
        lines.extend(["", "## Metrics", ""])
        model_calls = sum(1 for event in self.events if event.get("kind") == "model_call")
        adb_actions = sum(1 for event in self.events if event.get("kind") == "agent_step" and event.get("status") not in {"finished", "waiting"})
        runtime_requests = sum(1 for event in self.events if event.get("kind") == "runtime_request_created")
        steward_forwards = sum(
            1
            for event in self.events
            if event.get("kind") in {"runtime_request_routed", "runtime_response_delivered"} and event.get("via") == "steward"
        )
        pause_resume = sum(1 for event in self.events if event.get("kind") in {"agent_paused", "agent_resumed"})
        lines.extend(
            [
                f"- LLM calls: `{model_calls}`",
                f"- ADB agent actions: `{adb_actions}`",
                f"- Runtime requests: `{runtime_requests}`",
                f"- Steward forwarding events: `{steward_forwards}`",
                f"- Pause/resume events: `{pause_resume}`",
            ]
        )
        summary_path = self.run_dir / "summary.md"
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary_path
