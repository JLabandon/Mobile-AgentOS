from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..vlm_ui import DemoAgent, capture_agent_screen, snap_to_button_center
from ..adb import AdbClient
from ..job_scheduler import AgentRunSpec, FifoJobScheduler, IPCSpec
from ..jobs import Job, JobResult, JobType, ResourceRequirement
from ..report import RunReporter
from ..visualization.timeline import write_timeline
from ..vlm import GeminiScreenClient, prompt_hash


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK = "shop_payment_authorization"


class JobLevelExecutor:
    def __init__(self, *, adb: AdbClient, client: GeminiScreenClient, reporter: RunReporter, run_dir: Path) -> None:
        self.adb = adb
        self.client = client
        self.reporter = reporter
        self.run_dir = run_dir
        self.resident_agents: set[str] = set()

    def launch_agent(self, agent: DemoAgent) -> None:
        if agent.name in self.resident_agents:
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
        self.adb.launch_package_on_display(agent.package, agent.display_id)
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
        job = Job(
            job_type=JobType.OBSERVATION,
            agent_id=agent.name,
            phase=phase,
            display_id=agent.display_id,
            resources=(ResourceRequirement("display_observation", str(agent.display_id)),),
            payload={"step": step},
        )
        self._job_start(job, "OBSERVING")
        step_dir = self.run_dir / agent.name / phase / f"step_{step:02d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        screenshot = capture_agent_screen(self.adb, agent, step_dir / "screen.png")
        result = JobResult(job.job_id, job.job_type, agent.name, True, {"screenshot": str(screenshot)})
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
        prompt = self.client.build_action_prompt(
            screenshot_path=screenshot,
            agent_name=agent.name,
            app_label=agent.app_label,
            task_instruction=instruction,
            memory=memory,
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
            action = self.client.decide_ui_action(
                screenshot_path=screenshot,
                agent_name=agent.name,
                app_label=agent.app_label,
                task_instruction=instruction,
                memory=memory,
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
            prompt="<screenshot action>",
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
        job = Job(
            job_type=JobType.ACTION,
            agent_id=agent.name,
            phase=phase,
            display_id=agent.display_id,
            resources=(ResourceRequirement("display_input", str(agent.display_id)),),
            payload={"step": step, "action": action},
        )
        self._job_start(job, "ACTING", action=str(action.get("action", "")).lower())
        name = str(action.get("action", "")).lower()
        if name == "back":
            if agent.display_id == 0:
                self.adb.back()
            else:
                self.adb.back_display(agent.display_id)
            result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": "\nPrevious action: back."})
            self._job_finish(job, result)
            return result
        if name != "click":
            result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"unsupported primitive action: {action}")
            self._job_finish(job, result)
            return result
        if "x" in action and "y" in action:
            x = int(action["x"])
            y = int(action["y"])
        elif isinstance(action.get("point"), list) and len(action["point"]) == 2:
            x = int(action["point"][0])
            y = int(action["point"][1])
        else:
            result = JobResult(job.job_id, job.job_type, agent.name, False, error=f"click lacked coordinates: {action}")
            self._job_finish(job, result)
            return result
        original = (x, y)
        x, y, snap_reason = snap_to_button_center(screenshot, x, y)
        if (x, y) != original:
            self.reporter.event("coordinate_snap", runtime="job_level", agent=agent.name, phase=phase, from_xy=original, to_xy=(x, y), reason=snap_reason, job_id=job.job_id)
        if agent.display_id == 0:
            self.adb.tap(x, y)
        else:
            self.adb.tap_display(agent.display_id, x, y)
        result = JobResult(job.job_id, job.job_type, agent.name, True, {"memory": f"\nPrevious action: clicked at ({x}, {y})."})
        self._job_finish(job, result)
        return result

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
            message_kind="RuntimeOperationResponse",
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
    path = PROJECT_ROOT / "config" / "tasks" / "stage5_job_scheduler.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if task not in data:
        raise KeyError(f"task not found in {path}: {task}")
    return data[task]


def _stage5_agents(adb: AdbClient, task_config: dict[str, Any]) -> dict[str, DemoAgent]:
    apps_path = PROJECT_ROOT / "config" / "apps.json"
    app_profiles = json.loads(apps_path.read_text(encoding="utf-8"))
    displays = adb.list_displays()
    virtual_displays = [display for display in displays if display.display_id != 0 and display.surfaceflinger_id]
    agents: dict[str, DemoAgent] = {}
    for index, (agent_id, app_key) in enumerate(task_config.get("agents", {}).items()):
        profile = app_profiles[app_key]
        package = adb.pick_package(profile["package_candidates"])
        if index == 0:
            display_id = 0
            surfaceflinger_id = None
        else:
            if index - 1 >= len(virtual_displays):
                raise RuntimeError(f"not enough virtual displays for agent {agent_id}")
            display = virtual_displays[index - 1]
            display_id = display.display_id
            surfaceflinger_id = display.surfaceflinger_id
        agents[agent_id] = DemoAgent(
            name=agent_id,
            app_label=profile["label"],
            package=package,
            display_id=display_id,
            surfaceflinger_id=surfaceflinger_id,
        )
    return agents


def _scheduled_task_specs(task_config: dict[str, Any], agents: dict[str, DemoAgent]) -> tuple[list[AgentRunSpec], list[IPCSpec]]:
    specs = []
    for item in task_config.get("runs", []):
        agent = agents[item["agent_id"]]
        specs.append(
            AgentRunSpec(
                run_id=item["run_id"],
                agent=agent,
                instruction=item["instruction"],
                phase=item.get("phase", item["run_id"]),
                depends_on=tuple(item.get("depends_on", [])),
                max_steps=int(item.get("max_steps", 6)),
                launch=bool(item.get("launch", True)),
            )
        )
    ipc_specs = []
    for item in task_config.get("ipc", []):
        ipc_specs.append(
            IPCSpec(
                request_id=item["request_id"],
                source_run_id=item["source_run_id"],
                target_run_id=item["target_run_id"],
                source_agent=agents[item["source_agent_id"]],
                target_agent=agents[item["target_agent_id"]],
                payload_on_success=dict(item.get("success_payload", {})),
                payload_on_failure=dict(item.get("failure_payload", {})),
                request_summary=str(item.get("request_summary", "")),
            )
        )
    return specs, ipc_specs

def run_demo(*, mode: str, run_root: Path, task: str = TASK) -> Path:
    adb = AdbClient()
    adb.require_device()
    task_config = _load_task_config(task)
    for package_name in task_config.get("clear_packages", []):
        adb.clear_app_data(package_name)
    agents = _stage5_agents(adb, task_config)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / f"{task}_{mode}_{timestamp}"
    reporter = RunReporter(run_dir)
    reporter.event("runtime_start", runtime=mode, task=task, execution_backend="fifo_job_scheduler_vlm")
    client = GeminiScreenClient()
    executor = JobLevelExecutor(adb=adb, client=client, reporter=reporter, run_dir=run_dir)
    specs, ipc_specs = _scheduled_task_specs(task_config, agents)
    serial_order = tuple(task_config.get("serial_order", [])) if mode == "job_level_steward_serial" else ()
    max_workers = 1 if mode == "job_level_steward_serial" else 4
    scheduler = FifoJobScheduler(
        executor=executor,
        reporter=reporter,
        mode=mode,
        max_workers=max_workers,
        serial_order=serial_order,
        resource_capacity={"llm_worker:pool": max_workers},
    )
    scheduled = scheduler.run(specs=specs, ipc_specs=ipc_specs)
    final_run_id = str(task_config.get("final_run_id", ""))
    final_result = scheduled.outcomes.get(final_run_id)
    success = bool(
        scheduled.success
        and final_result
        and final_result.ok
        and str(task_config.get("success_contains", "")).lower() in final_result.message.lower()
    )
    finish_reason = final_result.message if final_result else scheduled.error
    reporter.event("runtime_finish", runtime=mode, task=task, success=success, reason=finish_reason)
    reporter.write_summary(task=task, runtime=mode, success=success, run_error=None if success else finish_reason)
    write_timeline(run_root, [run_dir])
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["job_level_steward_serial", "job_level_agentos"], required=True)
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--run-root", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--gemini-model", default="")
    args = parser.parse_args()
    if args.gemini_model:
        os.environ["GEMINI_MODEL"] = args.gemini_model
    print(run_demo(mode=args.mode, run_root=Path(args.run_root), task=args.task))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
