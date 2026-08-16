from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agents import SubTask
from .report import RunReporter
from .vlm import load_gemini_key


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDORED_RUNNER = PROJECT_ROOT / "vendor" / "mobilerun_runtime" / "run_agent.py"
DEFAULT_MOBILERUN_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


@dataclass(frozen=True)
class MobileRunResult:
    success: bool
    reason: str
    elapsed: float
    output_path: Path
    events: tuple[dict[str, Any], ...]


class MobileRunExecutor:
    def __init__(self, reporter: RunReporter, *, device: str | None = None) -> None:
        self.reporter = reporter
        self.device = device or os.environ.get("ANDROID_SERIAL")
        self.python = Path(os.environ.get("MOBILERUN_PYTHON", str(DEFAULT_MOBILERUN_PYTHON)))
        self.provider = os.environ.get("MOBILERUN_PROVIDER", "GoogleGenAI")
        self.model = os.environ.get("MOBILERUN_MODEL") or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        self.portal_mode = os.environ.get("MOBILERUN_PORTAL_MODE", "disabled")

    def run_subtask(
        self,
        *,
        agent_name: str,
        subtask: SubTask,
        run_dir: Path,
        runtime: str,
        completion_probe: Callable[[], str | None] | None = None,
        display_id: int | None = None,
        surfaceflinger_id: str | None = None,
    ) -> MobileRunResult:
        if not self.python.exists():
            raise RuntimeError(f"MobileRun Python runtime not found: {self.python}")
        if not VENDORED_RUNNER.exists():
            raise RuntimeError(f"vendored MobileRun runner not found: {VENDORED_RUNNER}")
        output_path = run_dir / "mobilerun" / runtime / agent_name / "result.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        goal = self._goal_text(agent_name=agent_name, subtask=subtask)
        effective_max_steps = self._effective_max_steps(subtask)
        cmd = [
            str(self.python),
            str(VENDORED_RUNNER),
            "--goal",
            goal,
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--max-steps",
            str(effective_max_steps),
            "--portal-mode",
            self.portal_mode,
            "--env-file",
            str(PROJECT_ROOT / ".env"),
            "--env-file",
            str(PROJECT_ROOT.parent / "agent_ipc_mvp" / ".env"),
            "--output",
            str(output_path),
        ]
        if self.device:
            cmd.extend(["--device", self.device])
        env = os.environ.copy()
        try:
            env.setdefault("GOOGLE_API_KEY", load_gemini_key())
        except Exception:
            pass
        if display_id is not None:
            env["MOBILERUN_ANDROID_DISPLAY_ID"] = str(display_id)
            if display_id == 0:
                env["MOBILERUN_ANDROID_DEFAULT_SCREENSHOT"] = "1"
        if surfaceflinger_id:
            env["MOBILERUN_ANDROID_SURFACEFLINGER_ID"] = str(surfaceflinger_id)

        started = time.monotonic()
        self.reporter.event(
            "mobilerun_subtask_start",
            runtime=runtime,
            agent=agent_name,
            goal=goal,
            model=self.model,
            portal_mode=self.portal_mode,
            configured_max_steps=subtask.max_steps,
            effective_max_steps=effective_max_steps,
            display_id=display_id,
            surfaceflinger_id=surfaceflinger_id,
        )
        completed = self._run_with_optional_probe(
            cmd=cmd,
            env=env,
            output_path=output_path,
            runtime=runtime,
            agent_name=agent_name,
            started=started,
            completion_probe=completion_probe,
        )
        elapsed = round(time.monotonic() - started, 3)
        if not output_path.exists():
            output_path.write_text(
                json.dumps(
                    {
                        "success": False,
                        "reason": "MobileRun runner did not produce result.json",
                        "elapsed": elapsed,
                        "events": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        result = MobileRunResult(
            success=bool(raw.get("success")),
            reason=str(raw.get("reason", "")),
            elapsed=float(raw.get("elapsed", elapsed) or elapsed),
            output_path=output_path,
            events=tuple(event for event in raw.get("events", []) if isinstance(event, dict)),
        )
        self.reporter.event(
            "mobilerun_subtask_finish",
            runtime=runtime,
            agent=agent_name,
            success=result.success,
            reason=result.reason,
            elapsed=result.elapsed,
            result=str(output_path),
            returncode=completed.returncode,
            stderr_tail=completed.stderr[-1200:],
        )
        return result

    def _run_with_optional_probe(
        self,
        *,
        cmd: list[str],
        env: dict[str, str],
        output_path: Path,
        runtime: str,
        agent_name: str,
        started: float,
        completion_probe: Callable[[], str | None] | None,
    ) -> subprocess.CompletedProcess[str]:
        if completion_probe is None:
            return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900, env=env)
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        while proc.poll() is None:
            time.sleep(3)
            evidence = completion_probe()
            if not evidence:
                continue
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            elapsed = round(time.monotonic() - started, 3)
            output_path.write_text(
                json.dumps(
                    {
                        "success": True,
                        "reason": f"External completion evidence satisfied: {evidence}",
                        "elapsed": elapsed,
                        "events": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            self.reporter.event(
                "mobilerun_subtask_external_completion",
                runtime=runtime,
                agent=agent_name,
                evidence=evidence,
                elapsed=elapsed,
            )
            return subprocess.CompletedProcess(cmd, proc.returncode or 0, stdout, stderr)
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(cmd, proc.returncode or 0, stdout, stderr)

    def _goal_text(self, *, agent_name: str, subtask: SubTask) -> str:
        terms = ", ".join(subtask.required_terms)
        suffix = (
            "\nCompletion evidence to make visible if applicable: "
            f"{terms}\nIf this evidence is already visible, complete the subtask. "
            "Do not keep searching for optional details unless the instruction says they are mandatory."
            if terms
            else ""
        )
        return (
            f"You are operating as the app-oriented agent named {agent_name}.\n"
            f"Complete this mobile subtask using the visible app UI:\n{subtask.instruction}\n"
            "Use normal mobile UI actions. If a dialog, picker, or form already shows the requested value, confirm it with the visible OK, Save, Done, or equivalent control and continue. "
            "Stop when the subtask is complete."
            f"{suffix}"
        )

    def _effective_max_steps(self, subtask: SubTask) -> int:
        return min(max(12, subtask.max_steps * 2), 30)
