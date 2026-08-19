from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..android.vlm_ui import DemoAgent, capture_agent_screen
from ..android.adb import AdbClient
from ..android.ui_tree import find_node, parse_ui_xml, prompt_snapshot
from ..kernel.scheduler import AgentRunSpec, FifoJobScheduler, IPCSpec
from ..kernel.jobs import Job, JobResult, JobType, ResourceRequirement
from ..report import RunReporter
from ..visualization.timeline import write_timeline
from ..model_clients.gemini import GeminiScreenClient, prompt_hash
from ..model_clients.deepseek import DeepSeekClient
from .environment import load_env_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK = "planned_shop_payment_authorization"


class JobLevelExecutor:
    def __init__(
        self,
        *,
        adb: AdbClient,
        client: GeminiScreenClient,
        reporter: RunReporter,
        run_dir: Path,
        agents: dict[str, DemoAgent] | None = None,
        instance_launch_flags: dict[str, str] | None = None,
    ) -> None:
        self.adb = adb
        self.client = client
        self.reporter = reporter
        self.run_dir = run_dir
        self.resident_agents: set[str] = set()
        self.agents = agents or {}
        self.instance_launch_flags = instance_launch_flags or {}
        self.instance_parent: dict[str, str] = {}
        self.allocated_display_ids = {agent.display_id for agent in self.agents.values()}
        self.available_instance_displays = [
            display
            for display in self.adb.list_displays()
            if display.display_id != 0
            and display.surfaceflinger_id
            and display.can_host_tasks
            and display.display_id not in self.allocated_display_ids
        ]

    def registry_context_for(self, agent: DemoAgent) -> str:
        peers = [peer for peer in self.agents.values() if peer.name != agent.name]
        lines = [
            "Runtime App Registry:",
            f"- current_agent: {agent.name}; app: {agent.app_label}; capabilities: {', '.join(agent.capabilities) or 'none'}; description: {agent.description or 'none'}",
        ]
        if agent.long_term_memory:
            lines.append("- current_agent_long_term_memory:")
            for item in agent.long_term_memory:
                lines.append(f"  - {item}")
        if peers:
            lines.append("- available_peer_agents:")
            for peer in peers:
                lines.append(
                    f"  - {peer.name}; app: {peer.app_label}; capabilities: {', '.join(peer.capabilities) or 'none'}; description: {peer.description or 'none'}"
                )
        else:
            lines.append("- available_peer_agents: none")
        return "\n".join(lines)

    def create_agent_instance(self, agent: DemoAgent, *, service_name: str, run_id: str, instance_index: int) -> DemoAgent:
        if not self.available_instance_displays:
            target_count = len([display_id for display_id in self.allocated_display_ids if display_id != 0]) + 1
            self.adb.require_task_hosting_displays(target_count)
            self.available_instance_displays = [
                display
                for display in self.adb.list_displays()
                if display.display_id != 0
                and display.surfaceflinger_id
                and display.can_host_tasks
                and display.display_id not in self.allocated_display_ids
            ]
        if not self.available_instance_displays:
            raise RuntimeError(f"no spare task-hosting display available for {service_name} instance {instance_index}")
        display = self.available_instance_displays.pop(0)
        self.allocated_display_ids.add(display.display_id)
        instance_name = f"{service_name}#{instance_index + 1}"
        instance = DemoAgent(
            name=instance_name,
            app_label=agent.app_label,
            package=agent.package,
            display_id=display.display_id,
            surfaceflinger_id=display.surfaceflinger_id,
            description=agent.description,
            capabilities=agent.capabilities,
            long_term_memory=agent.long_term_memory + (f"Runtime instance of {service_name}; preserve request-local UI state.",),
            status_oracle=agent.status_oracle,
        )
        self.instance_parent[instance.name] = service_name
        self.reporter.event(
            "app_instance_allocated",
            runtime="job_level",
            service_agent=service_name,
            instance_agent=instance.name,
            display_id=instance.display_id,
            surfaceflinger_id=instance.surfaceflinger_id,
            run_id=run_id,
        )
        return instance

    def launch_agent(self, agent: DemoAgent) -> None:
        if agent.name in self.resident_agents:
            if agent.display_id == 0:
                foreground = self.adb.foreground_package()
                if foreground != agent.package:
                    self.reporter.event(
                        "primary_display_resume_required",
                        runtime="job_level",
                        agent=agent.name,
                        expected_package=agent.package,
                        foreground_package=foreground or "",
                    )
                else:
                    self.reporter.event(
                        "display_switch_skipped",
                        runtime="job_level",
                        agent=agent.name,
                        display_id=agent.display_id,
                        package=agent.package,
                        reason="agent_app_already_foreground",
                    )
                    return
            else:
                self.reporter.event(
                    "display_switch_skipped",
                    runtime="job_level",
                    agent=agent.name,
                    display_id=agent.display_id,
                    package=agent.package,
                    reason="agent_app_already_resident",
                )
                return
        started = time.monotonic()
        self.reporter.event(
            "display_switch",
            runtime="job_level",
            agent=agent.name,
            display_id=agent.display_id,
            package=agent.package,
            purpose="launch_or_resume_agent_app",
        )
        self.reporter.state_event(
            agent.name,
            "SWITCH",
            runtime="job_level",
            display_id=agent.display_id,
            package=agent.package,
            purpose="launch_or_resume_agent_app",
        )
        service_name = self.instance_parent.get(agent.name, agent.name)
        launch_flags = self.instance_launch_flags.get(service_name) if agent.name in self.instance_parent else None
        if agent.display_id == 0:
            self.adb.launch_package(agent.package)
        else:
            self.adb.launch_package_on_display(agent.package, agent.display_id, flags=launch_flags)
        self.adb.settle(1.2)
        self.resident_agents.add(agent.name)
        elapsed = round(time.monotonic() - started, 3)
        self.reporter.event(
            "display_switch_done",
            runtime="job_level",
            agent=agent.name,
            display_id=agent.display_id,
            package=agent.package,
            elapsed=elapsed,
        )

    def observation_job(self, *, agent: DemoAgent, phase: str, step: int) -> JobResult:
        resources = (ResourceRequirement("foreground_display", "primary"),) if agent.display_id == 0 else ()
        job = Job(
            job_type=JobType.OBSERVATION,
            agent_id=agent.name,
            phase=phase,
            display_id=agent.display_id,
            resources=resources,
            payload={"step": step},
        )
        self._job_start(job, "OBSERVING")
        step_dir = self.run_dir / agent.name / phase / f"step_{step:02d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        screenshot = capture_agent_screen(self.adb, agent, step_dir / "screen.png")
        ui_xml = step_dir / "window_dump.xml"
        try:
            self.adb.dump_ui(ui_xml, display_id=agent.display_id)
        except Exception as exc:
            self.reporter.event(
                "ui_tree_dump_failed",
                runtime="job_level",
                agent=agent.name,
                display_id=agent.display_id,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            ui_xml = None
        output = {"screenshot": str(screenshot)}
        if ui_xml is not None:
            output["ui_xml"] = str(ui_xml)
        result = JobResult(job.job_id, job.job_type, agent.name, True, output)
        self._job_finish(job, result)
        return result

    def thinking_job(
        self,
        *,
        agent: DemoAgent,
        phase: str,
        step: int,
        screenshot: Path,
        instruction: str,
        memory: str,
    ) -> JobResult:
        job = Job(
            job_type=JobType.THINKING,
            agent_id=agent.name,
            phase=phase,
            display_id=agent.display_id,
            resources=(ResourceRequirement("llm_worker", "pool"),),
            payload={"step": step, "screenshot": str(screenshot)},
        )
        self._job_start(job, "THINKING")
        is_information_service = "late-bound runtime information request" in instruction.lower()
        ui_context = self._ui_context_for_screenshot(screenshot)
        model_memory = "\n\n".join(item for item in [self.registry_context_for(agent), ui_context, memory] if item)
        if is_information_service:
            prompt = self.client.build_information_response_prompt(
                screenshot_path=screenshot,
                agent_name=agent.name,
                app_label=agent.app_label,
                task_instruction=instruction,
                memory=model_memory,
            )
        else:
            prompt = self.client.build_action_prompt(
                screenshot_path=screenshot,
                agent_name=agent.name,
                app_label=agent.app_label,
                task_instruction=instruction,
                memory=model_memory,
            )
        prompt_path = screenshot.parent / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_digest = prompt_hash(prompt)
        self.reporter.event(
            "llm_submitted",
            runtime="job_level",
            agent=agent.name,
            phase=phase,
            step=step,
            screenshot=str(screenshot),
            display_id=agent.display_id,
            job_id=job.job_id,
            prompt_ref=str(prompt_path),
            prompt_hash=prompt_digest,
            prompt_chars=len(prompt),
        )
        try:
            if is_information_service:
                action = self.client.decide_information_response(
                    screenshot_path=screenshot,
                    agent_name=agent.name,
                    app_label=agent.app_label,
                    task_instruction=instruction,
                    memory=model_memory,
                )
            else:
                action = self.client.decide_ui_action(
                    screenshot_path=screenshot,
                    agent_name=agent.name,
                    app_label=agent.app_label,
                    task_instruction=instruction,
                    memory=model_memory,
                )
        except Exception as exc:
            result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"{exc.__class__.__name__}: {exc}")
            self._job_finish(job, result)
            return result
        if str(action.get("action", "")).lower() == "continue_navigation":
            self.reporter.event(
                "model_call",
                runtime="job_level",
                agent=agent.name,
                step=step,
                attempt=1,
                prompt="<screenshot information response>",
                prompt_ref=str(prompt_path),
                prompt_hash=prompt_digest,
                prompt_chars=len(prompt),
                response=json.dumps(action, ensure_ascii=False),
                job_id=job.job_id,
            )
            prompt = self.client.build_action_prompt(
                screenshot_path=screenshot,
                agent_name=agent.name,
                app_label=agent.app_label,
                task_instruction=instruction,
                memory=model_memory,
            )
            prompt_path = screenshot.parent / "prompt_navigation.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            prompt_digest = prompt_hash(prompt)
            self.reporter.event(
                "llm_submitted",
                runtime="job_level",
                agent=agent.name,
                phase=phase,
                step=step,
                screenshot=str(screenshot),
                display_id=agent.display_id,
                job_id=job.job_id,
                prompt_ref=str(prompt_path),
                prompt_hash=prompt_digest,
                prompt_chars=len(prompt),
                fallback_from="information_response",
            )
            try:
                action = self.client.decide_ui_action(
                    screenshot_path=screenshot,
                    agent_name=agent.name,
                    app_label=agent.app_label,
                    task_instruction=instruction,
                    memory=model_memory,
                )
            except Exception as exc:
                result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"{exc.__class__.__name__}: {exc}")
                self._job_finish(job, result)
                return result
        action_path = screenshot.parent / "action.json"
        action_path.write_text(json.dumps(action, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.reporter.event(
            "model_call",
            runtime="job_level",
            agent=agent.name,
            step=step,
            attempt=1,
            prompt="<screenshot information response>" if is_information_service else "<screenshot action>",
            prompt_ref=str(prompt_path),
            prompt_hash=prompt_digest,
            prompt_chars=len(prompt),
            response=json.dumps(action, ensure_ascii=False),
            job_id=job.job_id,
        )
        self.reporter.event("llm_completed", runtime="job_level", agent=agent.name, phase=phase, step=step, action=action, job_id=job.job_id)
        result = JobResult(job.job_id, job.job_type, agent.name, True, {"action": action, "action_path": str(action_path)})
        self._job_finish(job, result)
        return result

    def action_job(self, *, agent: DemoAgent, phase: str, step: int, screenshot: Path, action: dict[str, Any]) -> JobResult:
        resources = (ResourceRequirement("foreground_display", "primary"),) if agent.display_id == 0 else ()
        job = Job(
            job_type=JobType.ACTION,
            agent_id=agent.name,
            phase=phase,
            display_id=agent.display_id,
            resources=resources,
            payload={"step": step, "action": action},
        )
        self._job_start(job, "ACTING", action=str(action.get("action", "")).lower())
        name = str(action.get("action", "")).lower()
        if name in {"complete", "finish"}:
            message = str(action.get("message") or action.get("reason") or "completed")
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"complete": True, "memory": message})
            self._job_finish(job, result)
            return result
        if name == "fail":
            message = str(action.get("message") or action.get("reason") or "failed")
            result = JobResult(job.job_id, job.job_type, agent.name, False, error=message)
            self._job_finish(job, result)
            return result
        if name == "back":
            if agent.display_id == 0:
                self.adb.back()
            else:
                self.adb.back_display(agent.display_id)
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": "\nPrevious action: back."})
            self._job_finish(job, result)
            return result
        if name == "swipe":
            direction = str(action.get("direction") or "up").lower()
            if agent.display_id == 0:
                self.adb.swipe(direction)
            else:
                self.adb.swipe_display(agent.display_id, direction)
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": f"\nPrevious action: swiped {direction}."})
            self._job_finish(job, result)
            return result
        if name in {"input_text", "type_text", "input"}:
            text = str(action.get("text") or action.get("value") or "")
            if not text:
                result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"input_text lacked text: {action}")
                self._job_finish(job, result)
                return result
            point = self._element_point(action, screenshot=screenshot, editable_only=True) or self._action_point(action, screenshot=screenshot)
            if point is not None:
                x, y = point
                if agent.display_id == 0:
                    self.adb.tap(x, y)
                else:
                    self.adb.tap_display(agent.display_id, x, y)
                self.adb.settle(0.25)
            if agent.display_id == 0:
                self.adb.input_text(text)
            else:
                self.adb.input_text_display(agent.display_id, text)
            target_note = f" after tapping ({point[0]}, {point[1]})" if point is not None else ""
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": f"\nPrevious action: typed text {text!r}{target_note}."})
            self._job_finish(job, result)
            return result
        if name == "click_element":
            point = self._element_point(action, screenshot=screenshot)
            if point is None:
                result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"click_element could not resolve target: {action}")
                self._job_finish(job, result)
                return result
            x, y = point
            if agent.display_id == 0:
                self.adb.tap(x, y)
            else:
                self.adb.tap_display(agent.display_id, x, y)
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": f"\nPrevious action: clicked UI element at ({x}, {y})."})
            self._job_finish(job, result)
            return result
        if name == "click_area":
            point = self._area_center(action, screenshot=screenshot)
            if point is None:
                result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"click_area lacked bounds: {action}")
                self._job_finish(job, result)
                return result
            x, y = point
            if agent.display_id == 0:
                self.adb.tap(x, y)
            else:
                self.adb.tap_display(agent.display_id, x, y)
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": f"\nPrevious action: clicked area center at ({x}, {y})."})
            self._job_finish(job, result)
            return result
        if name != "click":
            result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"unsupported primitive action: {action}")
            self._job_finish(job, result)
            return result
        point = self._action_point(action, screenshot=screenshot)
        if point is None:
            result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"click lacked coordinates: {action}")
            self._job_finish(job, result)
            return result
        x, y = point
        if agent.display_id == 0:
            self.adb.tap(x, y)
        else:
            self.adb.tap_display(agent.display_id, x, y)
        result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": f"\nPrevious action: clicked at ({x}, {y})."})
        self._job_finish(job, result)
        return result

    def _area_center(self, action: dict[str, Any], *, screenshot: Path) -> tuple[int, int] | None:
        if all(key in action for key in ("x1", "y1", "x2", "y2")):
            x = round((int(action["x1"]) + int(action["x2"])) / 2)
            y = round((int(action["y1"]) + int(action["y2"])) / 2)
            return self._map_model_point_to_device(x, y, screenshot=screenshot)
        if isinstance(action.get("area"), list) and len(action["area"]) == 4:
            x1, y1, x2, y2 = [int(value) for value in action["area"]]
            return self._map_model_point_to_device(round((x1 + x2) / 2), round((y1 + y2) / 2), screenshot=screenshot)
        return None

    def _element_point(self, action: dict[str, Any], *, screenshot: Path, editable_only: bool = False) -> tuple[int, int] | None:
        xml_path = screenshot.with_name("window_dump.xml")
        if not xml_path.exists():
            return None
        try:
            nodes = parse_ui_xml(xml_path)
        except Exception:
            return None
        target_id = action.get("element_id", action.get("id"))
        if target_id is not None:
            try:
                target_id = int(target_id)
            except (TypeError, ValueError):
                target_id = None
        target_text = action.get("target_text") or action.get("text_label") or action.get("label")
        if target_text is not None:
            target_text = str(target_text)
        node = find_node(nodes, target_id=target_id, target_text=target_text, editable_only=editable_only)
        if node is None:
            return None
        return node.action_center or node.bounds.center

    def _action_point(self, action: dict[str, Any], *, screenshot: Path) -> tuple[int, int] | None:
        if "x" in action and "y" in action:
            return self._map_model_point_to_device(int(action["x"]), int(action["y"]), screenshot=screenshot)
        if isinstance(action.get("point"), list) and len(action["point"]) == 2:
            return self._map_model_point_to_device(int(action["point"][0]), int(action["point"][1]), screenshot=screenshot)
        if isinstance(action.get("coordinate"), list) and len(action["coordinate"]) == 2:
            return self._map_model_point_to_device(int(action["coordinate"][0]), int(action["coordinate"][1]), screenshot=screenshot)
        return None

    def _map_model_point_to_device(self, x: int, y: int, *, screenshot: Path) -> tuple[int, int]:
        model_screenshot = screenshot.with_name(f"{screenshot.stem}_model_grid{screenshot.suffix}")
        if not model_screenshot.exists():
            return x, y
        native_width, native_height = Image.open(screenshot).size
        model_width, model_height = Image.open(model_screenshot).size
        if model_width <= 0 or model_height <= 0:
            return x, y
        mapped_x = round(x * native_width / model_width)
        mapped_y = round(y * native_height / model_height)
        return mapped_x, mapped_y

    def _ui_context_for_screenshot(self, screenshot: Path) -> str:
        xml_path = screenshot.with_name("window_dump.xml")
        if not xml_path.exists():
            return ""
        try:
            nodes = parse_ui_xml(xml_path)
        except Exception:
            return ""
        actionable = [node for node in nodes if node.enabled and (node.clickable or node.editable or node.focused)]
        if not actionable:
            return ""
        return "Visible UI elements from Android accessibility tree. Prefer click_element/input_text with element_id when the target element is listed:\n" + prompt_snapshot(actionable, limit=40)

    def settle_job(self, *, agent: DemoAgent, phase: str, step: int, seconds: float = 1.0) -> JobResult:
        job = Job(
            job_type=JobType.SETTLE_WAIT,
            agent_id=agent.name,
            phase=phase,
            display_id=agent.display_id,
            resources=(ResourceRequirement("display_settle", str(agent.display_id)),),
            payload={"step": step, "seconds": seconds},
        )
        self._job_start(job, "SETTLING")
        time.sleep(seconds)
        result = JobResult(job.job_id, job.job_type, agent.name, True, {"seconds": seconds})
        self._job_finish(job, result)
        return result

    def completion_check(self, agent: DemoAgent) -> str | None:
        oracle = agent.status_oracle or {}
        uri = oracle.get("uri")
        success_contains = oracle.get("success_contains", "")
        if not uri or not success_contains:
            return None
        text = ""
        for attempt in range(5):
            proc = self.adb.shell("content", "query", "--uri", uri, timeout=15)
            text = (proc.stdout + proc.stderr).strip()
            if proc.returncode == 0 and success_contains.lower() in text.lower():
                return f"status oracle matched {success_contains}: {text}"
            if attempt < 4:
                time.sleep(0.25)
        return None

    def ipc_delivery_job(self, *, mode: str, request_id: str, source: DemoAgent, target: DemoAgent, message: str, payload: dict[str, Any], request_summary: str = "") -> JobResult:
        job = Job(
            job_type=JobType.IPC_DELIVERY,
            agent_id="runtime",
            phase="ipc_delivery",
            resources=(ResourceRequirement("ledger"), ResourceRequirement("mailbox", target.name)),
            payload={"request_id": request_id, "from": source.name, "to": target.name},
        )
        self._job_start(job, "IPC", runtime_agent="runtime")
        self.reporter.ipc_event(
            request_id=request_id,
            message_kind=str(payload.get("kind", "RuntimeInformationResponse")),
            status="delivered",
            from_agent=source.name,
            to_agent=target.name,
            mode=mode,
            via="steward" if mode == "job_level_steward_serial" else "peer",
            request_summary=request_summary,
            response_summary=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            evidence=message,
        )
        result = JobResult(job.job_id, job.job_type, "runtime", True, {"delivered": True, "payload": payload})
        self._job_finish(job, result)
        return result

    def _job_start(self, job: Job, state: str, **payload: Any) -> None:
        agent = str(payload.pop("runtime_agent", job.agent_id))
        self.reporter.event("job_start", runtime="job_level", job_id=job.job_id, job_type=job.job_type.value, agent=job.agent_id, phase=job.phase, display_id=job.display_id, resources=[r.__dict__ for r in job.resources], payload=job.payload)
        self.reporter.state_event(agent, state, runtime="job_level", job_id=job.job_id, job_type=job.job_type.value, phase=job.phase, display_id=job.display_id, **payload)

    def _job_finish(self, job: Job, result: JobResult) -> None:
        self.reporter.event("job_finish", runtime="job_level", job_id=job.job_id, job_type=job.job_type.value, agent=job.agent_id, phase=job.phase, ok=result.ok, output=result.output, error=result.error)


def _load_task_config(task: str) -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / "tasks" / "core_benchmark.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if task not in data:
        raise KeyError(f"task not found in {path}: {task}")
    return data[task]


def _app_profiles() -> dict[str, Any]:
    apps_path = PROJECT_ROOT / "config" / "apps.json"
    return json.loads(apps_path.read_text(encoding="utf-8"))


def _runtime_agents(
    adb: AdbClient,
    task_config: dict[str, Any],
    reporter: RunReporter | None = None,
    *,
    extra_display_slots: int = 0,
) -> dict[str, DemoAgent]:
    app_profiles = _app_profiles()
    non_primary_agents = max(0, len(task_config.get("agents", {})) - 1)
    needed_display_slots = non_primary_agents + max(0, extra_display_slots)
    display_slots = adb.require_task_hosting_displays(needed_display_slots) if needed_display_slots else []
    agents: dict[str, DemoAgent] = {}
    display_index = 0
    for index, (agent_id, app_key) in enumerate(task_config.get("agents", {}).items()):
        profile = app_profiles[app_key]
        package = adb.pick_package(profile["package_candidates"])
        display_policy = profile.get("display_policy", {}) if isinstance(profile.get("display_policy"), dict) else {}
        primary_only = display_policy.get("placement") == "primary_only"
        if index == 0 or primary_only:
            display_id = 0
            surfaceflinger_id = None
            if primary_only:
                reason = str(display_policy.get("reason", "registry_display_policy"))
                if reporter:
                    reporter.event(
                        "registry_display_policy_applied",
                        runtime="job_level",
                        agent=agent_id,
                        app=profile.get("label", app_key),
                        display_id=0,
                        reason=reason,
                    )
        else:
            if display_index >= len(display_slots):
                raise RuntimeError(f"not enough task-hosting display slots for agent {agent_id}")
            display = display_slots[display_index]
            display_index += 1
            display_id = display.display_id
            surfaceflinger_id = display.surfaceflinger_id
        agents[agent_id] = DemoAgent(
            name=agent_id,
            app_label=profile["label"],
            package=package,
            display_id=display_id,
            surfaceflinger_id=surfaceflinger_id,
            description=str(profile.get("description", "")),
            capabilities=tuple(str(item) for item in profile.get("capabilities", [])),
            long_term_memory=tuple(str(item) for item in profile.get("long_term_memory", [])),
            status_oracle=dict(profile.get("status_oracle", {})) if isinstance(profile.get("status_oracle"), dict) else None,
        )
    return agents


def _instance_policy_by_agent(task_config: dict[str, Any], overrides: dict[str, str]) -> dict[str, dict[str, Any]]:
    app_profiles = _app_profiles()
    policies: dict[str, dict[str, Any]] = {}
    for agent_id, app_key in task_config.get("agents", {}).items():
        profile_policy = dict(app_profiles.get(app_key, {}).get("instance_policy", {}))
        mode = overrides.get(agent_id) or overrides.get(app_key)
        if mode == "parallel":
            profile_policy.update({"default_mode": "parallel_instances", "supports_parallel_instances": True, "max_parallel_instances": max(2, int(profile_policy.get("max_parallel_instances", 2)))})
        elif mode == "single":
            profile_policy.update({"default_mode": "single_service_queue", "supports_parallel_instances": False, "max_parallel_instances": 1})
        profile_policy.setdefault("default_mode", "single_service_queue")
        profile_policy.setdefault("supports_parallel_instances", False)
        profile_policy.setdefault("max_parallel_instances", 1)
        profile_policy.setdefault("launch_flags", "0x18000000")
        policies[agent_id] = profile_policy
    return policies


def _parse_instance_policy_overrides(items: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid --instance-policy value: {item}")
        key, value = item.split("=", 1)
        value = value.strip().lower()
        if value not in {"single", "parallel"}:
            raise ValueError(f"instance policy must be single or parallel: {item}")
        overrides[key.strip()] = value
    return overrides

def _plan_runs_with_llm(task_config: dict[str, Any], agents: dict[str, DemoAgent], run_dir: Path, reporter: RunReporter) -> dict[str, Any]:
    goal = str(task_config.get("goal", "")).strip()
    if not goal:
        raise ValueError("task config without runs requires a goal")
    planner_dir = run_dir / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    app_lines = []
    for agent_id, agent in agents.items():
        app_lines.append(
            {
                "agent_id": agent_id,
                "app": agent.app_label,
                "capabilities": list(agent.capabilities),
                "description": agent.description,
            }
        )
    system = (
        "You are the task planner for Mobile AgentOS. Return JSON only. "
        "Given a user goal and a runtime app registry, create app-specific runs for a mobile runtime. "
        "Use app agents only for their own apps. Do not include hidden benchmark answers. "
        "The plan may schedule clear app-level dependencies up front and may leave runtime-dependent facts to AppAgents through runtime requests. "
        "Use app-level instructions and let AppAgents decide primitive UI actions from their screenshots. "
        "Subtask instructions must not guess which peer contains missing information. "
        "Mention a peer in an instruction only when the dependency graph declares that peer as a producer for this consumer. "
        "When no dependency is declared for a run, its instruction must be a plain app responsibility such as completing, inspecting, or reporting that app's own visible workflow. "
        "Do not write phrases such as 'from <peer>', 'ask <peer>', or 'if information from <peer> is needed' unless the same peer appears as a producer in dependencies. "
        "Use typed edges to describe graph structure. Add an edge only when the target run needs a concrete artifact, operation result, temporal order, service continuity, or instance continuity from the source run. "
        "Use fewer edges when runs can proceed independently; independent app workflows should remain unordered. "
        "For information or operation edges, producer run instructions should ask the producer to inspect, perform, or report its own app result; do not ask the producer to request the downstream consumer's action. "
        "Runtime requests are for dependencies discovered during execution that are not already covered by the typed edge graph. "
        "Return schema: {\"runs\":[{\"run_id\":\"...\",\"agent_id\":\"...\",\"phase\":\"...\",\"instruction\":\"...\",\"max_steps\":8}],"
        "\"edges\":[{\"edge_id\":\"...\",\"from_run_id\":\"...\",\"to_run_id\":\"...\",\"type\":\"information|operation|temporal|service_continuity|instance_continuity\",\"artifact\":\"short concrete artifact or result, empty for pure temporal edges\",\"required\":true,\"rationale\":\"short\"}],"
        "\"final_run_id\":\"...\",\"reason\":\"short\"}."
    )
    user = json.dumps(
        {
            "user_goal": goal,
            "runtime_app_registry": app_lines,
            "planner_contract": {
                "decomposition_unit": "app-level run",
                "instruction_level": "app responsibility, not button-level UI steps",
                "typed_edges": "use edges for information, operation, temporal, service_continuity, or instance_continuity constraints",
                "planned_artifacts": "information/operation edges must name the concrete artifact or result the target run will consume",
                "runtime_requests": "AppAgents can request information or operations during execution when screenshots reveal missing dependencies",
                "subtask_instructions": "describe only the assigned app responsibility. Leave peer selection to the AppAgent and Runtime App Registry unless an edge explicitly names a producer",
                "final_run_id": "the run whose completion represents the user goal",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    llm = DeepSeekClient()
    parsed: dict[str, Any] | None = None
    raw = ""
    active_user = user
    for attempt in range(1, 3):
        prompt_path = planner_dir / f"prompt_attempt_{attempt}.json"
        response_path = planner_dir / f"response_attempt_{attempt}.txt"
        prompt_path.write_text(json.dumps({"system": system, "user": active_user}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raw = llm.raw_chat(system=system, user=active_user, max_tokens=1200)
        response_path.write_text(raw, encoding="utf-8", errors="replace")
        candidate = llm.parse_json_content(raw)
        reporter.event("planner_model_call", runtime="job_level", attempt=attempt, prompt_ref=str(prompt_path), response_ref=str(response_path), raw_response=raw)
        issues = _planner_contract_issues(candidate, agents)
        if not issues:
            parsed = candidate
            (planner_dir / "prompt.json").write_text(prompt_path.read_text(encoding="utf-8"), encoding="utf-8")
            (planner_dir / "response.txt").write_text(raw, encoding="utf-8", errors="replace")
            break
        reporter.event("planner_contract_retry", runtime="job_level", attempt=attempt, issues=issues)
        active_user = user + "\n\nPlanner contract feedback from the runtime validator:\n" + "\n".join(f"- {issue}" for issue in issues)
    if parsed is None:
        raise ValueError(f"LLM planner violated planner contract after retry: {raw}")
    runs = parsed.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"LLM planner returned no runs: {parsed}")
    edges = _planner_edges(parsed)
    reporter.event("planner_typed_edges", runtime="job_level", edge_count=len(edges), edges=edges)
    return {
        "runs": runs,
        "edges": edges,
        "dependencies": _planned_dependencies({"edges": edges}),
        "final_run_id": str(parsed.get("final_run_id", "")),
        "reason": str(parsed.get("reason", "")),
    }


_PROVIDER_CAPABILITY_TERMS = ("retrieve", "search", "read", "inspect", "estimate")
_OPERATION_CAPABILITY_TERMS = ("authorize", "approve", "decline", "payment", "operation")
_STATUS_ARTIFACT_TERMS = ("completion", "complete", "completed", "status", "done", "finished", "success")
_EDGE_TYPES = {"information", "operation", "temporal", "service_continuity", "instance_continuity"}
_PRECEDENCE_EDGE_TYPES = {"information", "operation", "temporal"}
_IPC_EDGE_TYPES = {"information", "operation"}


def _planner_contract_issues(parsed: dict[str, Any], agents: dict[str, DemoAgent]) -> list[str]:
    runs = parsed.get("runs")
    if not isinstance(runs, list) or not runs:
        return ["Plan must include a non-empty runs list."]
    run_to_agent: dict[str, str] = {}
    for item in runs:
        if not isinstance(item, dict):
            return ["Each run must be an object."]
        run_id = str(item.get("run_id", "")).strip()
        agent_id = str(item.get("agent_id", "")).strip()
        if not run_id or not agent_id:
            return ["Each run must include run_id and agent_id."]
        if agent_id not in agents:
            return [f"Run {run_id} selects unknown agent_id {agent_id}."]
        run_to_agent[run_id] = agent_id
    issues: list[str] = []
    for item in _planner_edges(parsed):
        edge_type = str(item.get("type", "")).strip().lower()
        producer = str(item.get("from_run_id", "")).strip()
        consumer = str(item.get("to_run_id", "")).strip()
        artifact = str(item.get("artifact", "")).strip()
        if edge_type not in _EDGE_TYPES:
            issues.append(f"Edge {item} has unsupported type. Use one of {sorted(_EDGE_TYPES)}.")
            continue
        if producer not in run_to_agent or consumer not in run_to_agent:
            issues.append(f"Edge {item} references a run that is not in runs.")
            continue
        if edge_type == "temporal":
            continue
        producer_agent = agents[run_to_agent[producer]]
        capabilities = " ".join(producer_agent.capabilities).lower()
        artifact_lower = artifact.lower()
        is_status_only = any(term in artifact_lower for term in _STATUS_ARTIFACT_TERMS)
        can_produce_information = any(term in capabilities for term in _PROVIDER_CAPABILITY_TERMS)
        can_produce_operation = any(term in capabilities for term in _OPERATION_CAPABILITY_TERMS)
        if edge_type == "operation":
            if not can_produce_operation:
                issues.append(
                    f"Operation edge from {producer} to {consumer} uses producer agent {run_to_agent[producer]}, but the registry does not list operation-producing capability."
                )
            if not artifact:
                issues.append(f"Operation edge from {producer} to {consumer} must name the operation result artifact.")
            continue
        if edge_type == "information":
            if not artifact:
                issues.append(f"Information edge from {producer} to {consumer} must name the information artifact.")
            if is_status_only and not can_produce_information and not can_produce_operation:
                issues.append(
                    f"Information edge from {producer} to {consumer} transfers only completion/status as artifact. Use an information edge only for a real artifact the consumer needs. Independent app workflows should remain separate runs."
                )
    return issues


def _scheduled_task_specs(task_config: dict[str, Any], agents: dict[str, DemoAgent], run_dir: Path, reporter: RunReporter) -> tuple[list[AgentRunSpec], list[IPCSpec], str]:
    specs = []
    planned = task_config if task_config.get("runs") else _plan_runs_with_llm(task_config, agents, run_dir, reporter)
    run_items = [dict(item) for item in planned.get("runs", [])]
    task_max_steps = int(task_config.get("max_steps", 4))
    planned_edges = _planner_edges(planned)
    reporter.event("planner_typed_edges_compiled", runtime="job_level", edge_count=len(planned_edges), edges=planned_edges)
    planned_dependencies = _planned_dependencies({"edges": planned_edges})
    planned_artifacts_by_consumer = _apply_planned_edges(run_items, planned_edges)
    for item in run_items:
        if item["agent_id"] not in agents:
            raise ValueError(f"planner selected unknown agent_id: {item}")
        agent = agents[item["agent_id"]]
        instruction = str(item["instruction"])
        planned_artifacts = planned_artifacts_by_consumer.get(str(item["run_id"]), [])
        if planned_artifacts:
            instruction += "\nScheduler dependency context:\n"
            instruction += "\n".join(f"- {artifact}" for artifact in planned_artifacts)
            instruction += "\nBefore creating a runtime request, check whether the same needed result is already covered by this dependency context."
        specs.append(
            AgentRunSpec(
                run_id=item["run_id"],
                agent=agent,
                instruction=instruction,
                phase=item.get("phase", item["run_id"]),
                depends_on=tuple(item.get("depends_on", [])),
                max_steps=min(int(item.get("max_steps", task_max_steps)), task_max_steps),
                launch=bool(item.get("launch", True)),
            )
        )
    run_ids = {spec.run_id for spec in specs}
    for spec in specs:
        for dependency in spec.depends_on:
            if dependency not in run_ids:
                raise ValueError(f"planner selected unknown dependency {dependency} for {spec.run_id}")
    ipc_specs = []
    ipc_specs.extend(_compile_planned_dependencies(planned_dependencies, specs))
    for ipc in ipc_specs:
        target_spec = next((spec for spec in specs if spec.run_id == ipc.target_run_id), None)
        if target_spec and target_spec.depends_on and ipc.source_run_id not in target_spec.depends_on:
            raise ValueError(
                f"planner IPC direction is inconsistent with dependencies: {ipc.request_id} "
                f"source={ipc.source_run_id} target={ipc.target_run_id} target_depends_on={target_spec.depends_on}"
            )
    return specs, ipc_specs, str(planned.get("final_run_id") or task_config.get("final_run_id", ""))


def _planner_edges(planned: dict[str, Any]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    raw_edges = planned.get("edges", [])
    if isinstance(raw_edges, list):
        for item in raw_edges:
            if not isinstance(item, dict):
                continue
            edges.append(
                {
                    "edge_id": str(item.get("edge_id", f"planned_edge_{len(edges) + 1}")),
                    "from_run_id": str(item.get("from_run_id", item.get("from", ""))),
                    "to_run_id": str(item.get("to_run_id", item.get("to", ""))),
                    "type": str(item.get("type", "information")).lower(),
                    "artifact": str(item.get("artifact", "")),
                    "required": str(item.get("required", True)).lower(),
                    "rationale": str(item.get("rationale", "")),
                }
            )
    raw_dependencies = planned.get("dependencies", [])
    if isinstance(raw_dependencies, list):
        for item in raw_dependencies:
            if not isinstance(item, dict):
                continue
            edge_type = str(item.get("kind", "information")).lower()
            edges.append(
                {
                    "edge_id": str(item.get("dependency_id", f"planned_edge_{len(edges) + 1}")),
                    "from_run_id": str(item.get("producer_run_id", "")),
                    "to_run_id": str(item.get("consumer_run_id", "")),
                    "type": "operation" if edge_type == "operation" else "information",
                    "artifact": str(item.get("artifact", "")),
                    "required": "true",
                    "rationale": str(item.get("rationale", "legacy dependency")),
                }
            )
    return edges


def _planned_dependencies(planned: dict[str, Any]) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    for edge in _planner_edges(planned):
        edge_type = edge["type"].lower()
        if edge_type not in _IPC_EDGE_TYPES:
            continue
        dependencies.append(
            {
                "dependency_id": edge["edge_id"],
                "producer_run_id": edge["from_run_id"],
                "consumer_run_id": edge["to_run_id"],
                "artifact": edge["artifact"],
                "kind": edge_type,
            }
        )
    return dependencies


def _apply_planned_edges(run_items: list[dict[str, Any]], edges: list[dict[str, str]]) -> dict[str, list[str]]:
    artifacts_by_consumer: dict[str, list[str]] = {}
    for edge in edges:
        producer = edge["from_run_id"]
        consumer = edge["to_run_id"]
        edge_type = edge["type"].lower()
        artifact = edge["artifact"]
        if not producer or not consumer:
            continue
        if edge_type in _PRECEDENCE_EDGE_TYPES:
            for item in run_items:
                if str(item.get("run_id", "")) != consumer:
                    continue
                deps = [str(dep) for dep in item.get("depends_on", [])]
                if producer not in deps:
                    deps.append(producer)
                item["depends_on"] = deps
        if edge_type in _IPC_EDGE_TYPES and artifact:
            artifacts_by_consumer.setdefault(consumer, []).append(f"{producer}: {edge_type} artifact: {artifact}")
    return artifacts_by_consumer


def _compile_planned_dependencies(dependencies: list[dict[str, str]], specs: list[AgentRunSpec]) -> list[IPCSpec]:
    specs_by_run_id = {spec.run_id: spec for spec in specs}
    ipc_specs: list[IPCSpec] = []
    for dependency in dependencies:
        producer = dependency["producer_run_id"]
        consumer = dependency["consumer_run_id"]
        if not producer or not consumer:
            continue
        producer_spec = specs_by_run_id.get(producer)
        consumer_spec = specs_by_run_id.get(consumer)
        if not producer_spec or not consumer_spec:
            raise ValueError(f"planner selected unknown planned dependency: {dependency}")
        kind = "RuntimeOperationResponse" if dependency["kind"].lower() == "operation" else "RuntimeInformationResponse"
        ipc_specs.append(
            IPCSpec(
                request_id=dependency["dependency_id"],
                source_run_id=producer,
                target_run_id=consumer,
                source_agent=producer_spec.agent,
                target_agent=consumer_spec.agent,
                payload_on_success={"kind": kind, "status": "success", "artifact": dependency["artifact"]},
                payload_on_failure={"kind": kind, "status": "failed", "artifact": dependency["artifact"]},
                request_summary=dependency["artifact"],
            )
        )
    return ipc_specs


def _covers_all_runs(specs: list[AgentRunSpec], final_run_id: str) -> bool:
    if not final_run_id:
        return False
    deps_by_run = {spec.run_id: set(spec.depends_on) for spec in specs}
    if final_run_id not in deps_by_run:
        return False
    covered = {final_run_id}
    stack = [final_run_id]
    while stack:
        current = stack.pop()
        for dependency in deps_by_run.get(current, set()):
            if dependency not in covered:
                covered.add(dependency)
                stack.append(dependency)
    return covered == set(deps_by_run)


def run_demo(*, mode: str, run_root: Path, task: str = TASK, instance_policy_overrides: dict[str, str] | None = None) -> Path:
    load_env_file(PROJECT_ROOT / ".env")
    load_env_file(PROJECT_ROOT.parent / "agent_ipc_mvp" / ".env")
    adb = AdbClient()
    adb.require_device()
    task_config = _load_task_config(task)
    for package_name in task_config.get("clear_packages", []):
        adb.clear_app_data(package_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / f"{task}_{mode}_{timestamp}"
    reporter = RunReporter(run_dir)
    reporter.event("runtime_start", runtime=mode, task=task, execution_backend="fifo_job_scheduler_vlm")
    instance_policies = _instance_policy_by_agent(task_config, instance_policy_overrides or {})
    reporter.event("runtime_registry_instance_policy", runtime=mode, policies=instance_policies)
    service_capacity = {
        agent_id: int(policy.get("max_parallel_instances", 1))
        for agent_id, policy in instance_policies.items()
        if mode != "job_level_steward_serial" and policy.get("supports_parallel_instances")
    }
    extra_display_slots = sum(max(0, capacity - 1) for capacity in service_capacity.values())
    agents = _runtime_agents(adb, task_config, reporter, extra_display_slots=extra_display_slots)
    client = GeminiScreenClient()
    instance_launch_flags = {
        agent_id: str(policy.get("launch_flags", "0x18000000"))
        for agent_id, policy in instance_policies.items()
        if mode != "job_level_steward_serial" and policy.get("supports_parallel_instances")
    }
    executor = JobLevelExecutor(adb=adb, client=client, reporter=reporter, run_dir=run_dir, agents=agents, instance_launch_flags=instance_launch_flags)
    specs, ipc_specs, planned_final_run_id = _scheduled_task_specs(task_config, agents, run_dir, reporter)
    spec_run_ids = {spec.run_id for spec in specs}
    configured_final_run_id = str(task_config.get("final_run_id") or "")
    final_run_id = ""
    if planned_final_run_id in spec_run_ids and _covers_all_runs(specs, planned_final_run_id):
        final_run_id = planned_final_run_id
    elif configured_final_run_id in spec_run_ids and _covers_all_runs(specs, configured_final_run_id):
        final_run_id = configured_final_run_id
    elif planned_final_run_id or configured_final_run_id:
        reporter.event(
            "final_run_id_ignored",
            runtime=mode,
            planned_final_run_id=planned_final_run_id,
            configured_final_run_id=configured_final_run_id,
            reason="final run does not cover all app-level runs in the dependency graph",
        )
    serial_order = tuple(task_config.get("serial_order", []))
    if mode == "job_level_steward_serial" and not serial_order:
        serial_order = tuple(spec.run_id for spec in specs)
    if mode != "job_level_steward_serial":
        serial_order = ()
    max_workers = 1 if mode == "job_level_steward_serial" else 4
    scheduler = FifoJobScheduler(
        executor=executor,
        reporter=reporter,
        mode=mode,
        max_workers=max_workers,
        serial_order=serial_order,
        resource_capacity={
            "llm_worker:pool": max_workers,
            "foreground_display:primary": 1,
            "display_slot:task_hosting": len(executor.available_instance_displays),
        },
        service_agents=agents,
        service_capacity=service_capacity,
        final_run_id=final_run_id,
    )
    scheduled = scheduler.run(specs=specs, ipc_specs=ipc_specs)
    combined_message = "\n".join(outcome.message for outcome in scheduled.outcomes.values() if outcome.ok)
    final_result = scheduled.outcomes.get(final_run_id) if final_run_id else None
    success_target = final_result.message if final_result else combined_message
    success = bool(
        scheduled.success
        and success_target
        and str(task_config.get("success_contains", "")).lower() in success_target.lower()
    )
    finish_reason = success_target if success_target else scheduled.error
    reporter.event("runtime_finish", runtime=mode, task=task, success=success, reason=finish_reason)
    reporter.write_summary(task=task, runtime=mode, success=success, run_error=None if success else finish_reason)
    timeline_run_dirs = sorted(
        candidate
        for candidate in run_root.glob(f"{task}_*")
        if candidate.is_dir() and (candidate / "state_timeline.jsonl").exists()
    )
    write_timeline(run_root, timeline_run_dirs or [run_dir])
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["job_level_steward_serial", "job_level_agentos"], required=True)
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--run-root", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--gemini-model", default="")
    parser.add_argument("--instance-policy", action="append", default=[], help="Override registry instance policy, e.g. keep_agent=parallel or keep=single")
    args = parser.parse_args()
    if args.gemini_model:
        os.environ["GEMINI_MODEL"] = args.gemini_model
    print(run_demo(mode=args.mode, run_root=Path(args.run_root), task=args.task, instance_policy_overrides=_parse_instance_policy_overrides(args.instance_policy)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
