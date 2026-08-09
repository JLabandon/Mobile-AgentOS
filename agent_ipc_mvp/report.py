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
            elif kind == "error":
                lines.append(f"- `error`: {event.get('message')}")
        summary_path = self.run_dir / "summary.md"
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary_path
