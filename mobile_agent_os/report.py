from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import time


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
        self.ipc_ledger_path = self.run_dir / "ipc_ledger.jsonl"
        self.state_timeline_path = self.run_dir / "state_timeline.jsonl"
        self.metrics_path = self.run_dir / "metrics.json"
        self.events: list[dict[str, Any]] = []
        self.ipc_events: list[dict[str, Any]] = []
        self.state_events: list[dict[str, Any]] = []
        self.started_monotonic = time.monotonic()

    def event(self, kind: str, **payload: Any) -> None:
        item = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "t": round(time.monotonic() - self.started_monotonic, 3),
            "kind": kind,
            **payload,
        }
        self.events.append(item)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")

    def state_event(self, agent: str, state: str, **payload: Any) -> None:
        item = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "t": round(time.monotonic() - self.started_monotonic, 3),
            "agent": agent,
            "state": state,
            **payload,
        }
        self.state_events.append(item)
        with self.state_timeline_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")

    def ipc_event(
        self,
        *,
        request_id: str,
        message_kind: str,
        status: str,
        from_agent: str,
        to_agent: str,
        mode: str = "",
        via: str = "",
        request_summary: str = "",
        response_summary: str = "",
        evidence: str = "",
        evidence_ref: str = "",
        policy_decision: str = "not_checked",
        payload_ref: str = "",
        steward_visible: bool = True,
        user_visible: bool = True,
    ) -> None:
        request_summary, request_payload_ref = self._ipc_text(request_id, "request", request_summary, payload_ref)
        response_summary, response_payload_ref = self._ipc_text(request_id, "response", response_summary, payload_ref)
        evidence, evidence_payload_ref = self._ipc_text(request_id, "evidence", evidence, payload_ref)
        payload_ref = payload_ref or response_payload_ref or request_payload_ref or evidence_payload_ref
        evidence_ref = evidence_ref or evidence_payload_ref
        item = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "request_id": request_id,
            "message_kind": message_kind,
            "status": status,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "mode": mode,
            "via": via,
            "request_summary": request_summary,
            "response_summary": response_summary,
            "evidence": evidence,
            "evidence_ref": evidence_ref,
            "policy_decision": policy_decision,
            "payload_ref": payload_ref,
            "steward_visible": steward_visible,
            "user_visible": user_visible,
        }
        self.ipc_events.append(item)
        with self.ipc_ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")

    def _ipc_text(self, request_id: str, field: str, text: str, existing_ref: str, *, limit: int = 240) -> tuple[str, str]:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value, ""
        payload_dir = self.run_dir / "ipc_payloads"
        payload_dir.mkdir(parents=True, exist_ok=True)
        path = payload_dir / f"{request_id}_{field}.txt"
        path.write_text(value + "\n", encoding="utf-8")
        return value[: limit - 1].rstrip() + "…", existing_ref or str(path)

    def query_ipc_ledger(self, *, request_id: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
        events = self.ipc_events
        if request_id:
            events = [event for event in events if event.get("request_id") == request_id]
        if agent:
            events = [event for event in events if agent in {event.get("from_agent"), event.get("to_agent")}]
        return events

    def metrics(self, *, task: str, runtime: str, success: bool, run_error: str | None = None) -> dict[str, Any]:
        elapsed = round(time.monotonic() - self.started_monotonic, 3)
        model_calls = sum(1 for event in self.events if event.get("kind") == "model_call")
        adb_actions = sum(1 for event in self.events if self._is_ui_action_event(event))
        adb_actions += sum(1 for event in self.state_events if self._is_ui_action_state(event))
        steward_turns = sum(1 for event in self.events if event.get("kind") in {"steward_plan", "runtime_request_routed", "runtime_response_delivered"} and event.get("via", "steward") == "steward")
        ipc_messages = len(self.ipc_events)
        display_switches = [event for event in self.events if event.get("kind") == "display_switch"]
        app_switches = len(display_switches) if display_switches else sum(1 for event in self.events if event.get("kind") in {"app_launch", "app_resume"})
        wait_peer_time = 0.0
        wait_started: dict[str, float] = {}
        for event in self.state_events:
            agent = str(event.get("agent"))
            state = event.get("state")
            t_value = float(event.get("t", 0.0))
            if state == "WAIT_PEER":
                wait_started[agent] = t_value
            elif agent in wait_started and state in {"READY", "RUNNING", "DONE", "FAILED"}:
                wait_peer_time += max(0.0, t_value - wait_started.pop(agent))
        agentos_metrics = self._agentos_metrics(elapsed)
        metrics = {
            "task": task,
            "runtime": runtime,
            "success": success,
            "wall_clock_time": elapsed,
            "llm_calls": model_calls,
            "adb_ui_actions": adb_actions,
            "steward_turns": steward_turns,
            "ipc_messages": ipc_messages,
            "wait_peer_time": round(wait_peer_time, 3),
            "app_switches": app_switches,
            "total_runtime_events": len(self.events) + len(self.state_events) + len(self.ipc_events),
            "run_error": run_error,
            "run_dir": str(self.run_dir),
            **agentos_metrics,
        }
        self.metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return metrics

    def _agentos_metrics(self, elapsed: float) -> dict[str, Any]:
        intervals = self._state_intervals(default_end=elapsed)
        thinking = [item for item in intervals if item["state"] == "THINKING"]
        observing = [item for item in intervals if item["state"] == "OBSERVING"]
        acting = [item for item in intervals if item["state"] == "ACTING"]
        settling = [item for item in intervals if item["state"] == "SETTLING"]
        active = [item for item in intervals if item["state"] in {"OBSERVING", "ACTING", "SETTLING"}]
        ready_to_act = [item for item in intervals if item["state"] == "READY_TO_ACT"]
        wait_resource = [item for item in intervals if item["state"] == "WAIT_RESOURCE"]
        switch = [item for item in intervals if item["state"] == "SWITCH"]

        llm_thinking_time = sum(item["end"] - item["start"] for item in thinking)
        parallel_thinking_overlap_time = self._parallel_state_overlap(thinking)
        observe_overlap_time = self._parallel_state_overlap(observing)
        action_overlap_time = self._parallel_state_overlap(acting)
        settle_overlap_time = self._parallel_state_overlap(settling)
        ready_to_act_wait_time = sum(item["end"] - item["start"] for item in ready_to_act)
        resource_wait_time = sum(item["end"] - item["start"] for item in wait_resource)
        switch_time = sum(item["end"] - item["start"] for item in switch)
        llm_overlap_time = 0.0
        for think in thinking:
            for other in active:
                if other["agent"] == think["agent"]:
                    continue
                overlap = min(think["end"], other["end"]) - max(think["start"], other["start"])
                llm_overlap_time += max(0.0, overlap)
        cross_stage_overlap_time = self._cross_stage_overlap(
            thinking,
            [*observing, *acting, *settling],
        )

        display_occupancy: dict[str, float] = {}
        for item in active:
            display_id = item.get("display_id")
            if display_id is None:
                continue
            key = str(display_id)
            display_occupancy[key] = display_occupancy.get(key, 0.0) + max(0.0, item["end"] - item["start"])

        guard_events = [event for event in self.events if event.get("kind") == "action_guard"]
        guard_pass = sum(1 for event in guard_events if event.get("result") == "pass")
        guard_fail = sum(1 for event in guard_events if event.get("result") == "fail")
        display_actions = [event for event in self.events if event.get("kind") == "agent_step" and event.get("display_id") is not None]
        successful_display_actions = sum(1 for event in display_actions if event.get("status") not in {"failed"})
        display_success_rate = (
            round(successful_display_actions / len(display_actions), 3)
            if display_actions
            else None
        )
        return {
            "llm_thinking_time": round(llm_thinking_time, 3),
            "llm_overlap_time": round(llm_overlap_time, 3),
            "parallel_thinking_overlap_time": round(parallel_thinking_overlap_time, 3),
            "observe_overlap_time": round(observe_overlap_time, 3),
            "action_overlap_time": round(action_overlap_time, 3),
            "settle_overlap_time": round(settle_overlap_time, 3),
            "cross_stage_overlap_time": round(cross_stage_overlap_time, 3),
            "ready_to_act_wait_time": round(ready_to_act_wait_time, 3),
            "resource_wait_time": round(resource_wait_time, 3),
            "switch_time": round(switch_time, 3),
            **self._job_counts(),
            "display_occupancy_by_slot": {key: round(value, 3) for key, value in sorted(display_occupancy.items())},
            "fast_guard_pass_count": guard_pass,
            "fast_guard_fail_count": guard_fail,
            "full_reobserve_count": sum(1 for event in self.events if event.get("kind") == "reobserve_required"),
            "stale_action_count": guard_fail,
            "display_targeted_action_success_rate": display_success_rate,
            "useful_parallel_progress_ratio": round(llm_overlap_time / elapsed, 3) if elapsed > 0 else 0.0,
        }

    def _is_ui_action_event(self, event: dict[str, Any]) -> bool:
        if event.get("kind") != "agent_step":
            return False
        action = event.get("action")
        if not isinstance(action, dict):
            return False
        return action.get("action") in {"click", "click_area", "input", "input_text", "type_text", "swipe", "back"}

    def _is_ui_action_state(self, event: dict[str, Any]) -> bool:
        if event.get("state") != "ACTING":
            return False
        return event.get("action") in {"click", "click_area", "input", "input_text", "type_text", "swipe", "back"}

    def _parallel_state_overlap(self, intervals: list[dict[str, Any]]) -> float:
        points: list[tuple[float, int]] = []
        for item in intervals:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
            if end <= start:
                continue
            points.append((start, 1))
            points.append((end, -1))
        points.sort(key=lambda item: (item[0], -item[1]))
        active = 0
        previous: float | None = None
        overlap = 0.0
        for t_value, delta in points:
            if previous is not None and active >= 2:
                overlap += max(0.0, t_value - previous)
            active += delta
            previous = t_value
        return overlap

    def _cross_stage_overlap(self, primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> float:
        overlap = 0.0
        for first in primary:
            for second in secondary:
                if first["agent"] == second["agent"]:
                    continue
                overlap += max(0.0, min(first["end"], second["end"]) - max(first["start"], second["start"]))
        return overlap

    def _job_counts(self) -> dict[str, int]:
        counts = {
            "observation_jobs": 0,
            "thinking_jobs": 0,
            "action_jobs": 0,
            "settle_jobs": 0,
            "ipc_delivery_jobs": 0,
        }
        by_type = {
            "ObservationJob": "observation_jobs",
            "ThinkingJob": "thinking_jobs",
            "ActionJob": "action_jobs",
            "SettleWaitJob": "settle_jobs",
            "IPCDeliveryJob": "ipc_delivery_jobs",
        }
        for event in self.events:
            if event.get("kind") != "job_finish":
                continue
            key = by_type.get(str(event.get("job_type", "")))
            if key:
                counts[key] += 1
        return counts

    def _state_intervals(self, *, default_end: float) -> list[dict[str, Any]]:
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for event in self.state_events:
            by_agent.setdefault(str(event.get("agent")), []).append(event)
        intervals: list[dict[str, Any]] = []
        for agent, events in by_agent.items():
            sorted_events = sorted(events, key=lambda item: float(item.get("t", 0.0)))
            for idx, event in enumerate(sorted_events):
                start = float(event.get("t", 0.0))
                end = float(sorted_events[idx + 1].get("t", default_end)) if idx + 1 < len(sorted_events) else default_end
                intervals.append(
                    {
                        "agent": agent,
                        "state": str(event.get("state", "")),
                        "start": start,
                        "end": max(start, end),
                        "display_id": event.get("display_id"),
                        "job_type": event.get("job_type"),
                    }
                )
        return intervals

    def write_summary(self, *, task: str, success: bool, runtime: str = "", run_error: str | None = None) -> Path:
        metrics = self.metrics(task=task, runtime=runtime, success=success, run_error=run_error)
        lines = [
            "# Mobile AgentOS Run Summary",
            "",
            f"- Task: `{task}`",
            f"- Runtime: `{runtime}`",
            f"- Success: `{success}`",
            f"- Run directory: `{self.run_dir}`",
        ]
        if run_error:
            lines.append(f"- Error: `{run_error}`")
        lines.extend(
            [
                f"- Trace: `{self.trace_path}`",
                f"- IPC ledger: `{self.ipc_ledger_path}`",
                f"- State timeline: `{self.state_timeline_path}`",
                f"- Metrics: `{self.metrics_path}`",
            ]
        )
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
        for key in [
            "wall_clock_time",
            "llm_calls",
            "adb_ui_actions",
            "steward_turns",
            "ipc_messages",
            "wait_peer_time",
            "app_switches",
            "total_runtime_events",
            "llm_thinking_time",
            "llm_overlap_time",
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
            "ready_to_act_wait_time",
            "resource_wait_time",
            "fast_guard_pass_count",
            "fast_guard_fail_count",
            "full_reobserve_count",
            "useful_parallel_progress_ratio",
        ]:
            lines.append(f"- {key}: `{metrics.get(key)}`")
        summary_path = self.run_dir / "summary.md"
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary_path
