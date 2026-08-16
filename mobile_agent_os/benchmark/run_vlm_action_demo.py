from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from ..adb import AdbClient
from ..report import RunReporter
from ..visualization.timeline import write_timeline
from ..vlm import GeminiScreenClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DemoAgent:
    name: str
    app_label: str
    package: str
    display_id: int
    surfaceflinger_id: str | None = None


def _capture(adb: AdbClient, agent: DemoAgent, out_path: Path) -> Path:
    if agent.display_id == 0:
        return adb.screenshot(out_path)
    if agent.surfaceflinger_id:
        return adb.screenshot_display(agent.surfaceflinger_id, out_path)
    return adb.screenshot_display(agent.display_id, out_path)


def _launch(adb: AdbClient, agent: DemoAgent) -> None:
    adb.force_stop(agent.package)
    adb.launch_package_on_display(agent.package, agent.display_id)
    adb.settle(1.2)


def _tap(adb: AdbClient, agent: DemoAgent, x: int, y: int) -> None:
    if agent.display_id == 0:
        adb.tap(x, y)
    else:
        adb.tap_display(agent.display_id, x, y)
    adb.settle(0.8)


def _snap_to_button_center(screenshot: Path, x: int, y: int) -> tuple[int, int, str]:
    image = Image.open(screenshot).convert("RGB")
    width, height = image.size
    pixels = image.load()
    rows: list[tuple[int, int, int, int]] = []
    for yy in range(0, height, 2):
        xs = [
            xx
            for xx in range(0, width, 2)
            if 170 <= pixels[xx, yy][0] <= 235
            and 170 <= pixels[xx, yy][1] <= 235
            and 170 <= pixels[xx, yy][2] <= 235
            and max(pixels[xx, yy]) - min(pixels[xx, yy]) < 12
        ]
        if len(xs) * 2 >= width * 0.35:
            rows.append((yy, min(xs), max(xs), len(xs) * 2))
    candidates: list[tuple[int, int, int, int]] = []
    start = None
    left = width
    right = 0
    previous = -10
    for yy, row_left, row_right, _count in rows:
        if start is None or yy - previous > 4:
            if start is not None and previous - start >= 35:
                candidates.append((left, start, right, previous))
            start = yy
            left = row_left
            right = row_right
        else:
            left = min(left, row_left)
            right = max(right, row_right)
        previous = yy
    if start is not None and previous - start >= 35:
        candidates.append((left, start, right, previous))
    candidates = [
        item for item in candidates
        if item[2] - item[0] >= width * 0.45 and 35 <= item[3] - item[1] <= 140
    ]
    containing = [item for item in candidates if item[0] <= x <= item[2] and item[1] <= y <= item[3]]
    if containing or not candidates:
        return x, y, "unchanged"
    horizontal = [item for item in candidates if item[0] <= x <= item[2]]
    pool = horizontal or candidates
    nearest = min(pool, key=lambda item: abs(((item[1] + item[3]) // 2) - y))
    snapped = ((nearest[0] + nearest[2]) // 2, (nearest[1] + nearest[3]) // 2)
    return snapped[0], snapped[1], f"snapped_to_button:{nearest}"


def run_action_loop(
    *,
    adb: AdbClient,
    client: GeminiScreenClient,
    reporter: RunReporter,
    agent: DemoAgent,
    instruction: str,
    run_dir: Path,
    phase: str,
    memory: str = "",
    max_steps: int = 6,
) -> tuple[bool, str]:
    _launch(adb, agent)
    local_memory = memory
    for step in range(1, max_steps + 1):
        step_dir = run_dir / agent.name / phase / f"step_{step:02d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        reporter.state_event(agent.name, "OBSERVING", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id)
        screenshot = _capture(adb, agent, step_dir / "screen.png")
        reporter.state_event(agent.name, "THINKING", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id)
        reporter.event("llm_submitted", runtime="vlm_action_demo", agent=agent.name, phase=phase, step=step, screenshot=str(screenshot), display_id=agent.display_id)
        try:
            action = client.decide_ui_action(
                screenshot_path=screenshot,
                agent_name=agent.name,
                app_label=agent.app_label,
                task_instruction=instruction,
                memory=local_memory,
            )
        except Exception as exc:
            reporter.state_event(agent.name, "FAILED", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id)
            return False, f"{exc.__class__.__name__}: {exc}"
        (step_dir / "action.json").write_text(json.dumps(action, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        reporter.event("model_call", runtime="vlm_action_demo", agent=agent.name, step=step, attempt=1, prompt="<screenshot action>", response=json.dumps(action, ensure_ascii=False))
        reporter.event("llm_completed", runtime="vlm_action_demo", agent=agent.name, phase=phase, step=step, action=action)
        name = str(action.get("action", "")).lower()
        if name == "complete":
            message = str(action.get("message", "complete"))
            reporter.state_event(agent.name, "DONE", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id, reason=message)
            return True, message
        if name == "fail":
            message = str(action.get("message", "failed"))
            reporter.state_event(agent.name, "FAILED", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id, reason=message)
            return False, message
        if name == "back":
            reporter.state_event(agent.name, "ACTING", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id, action=name)
            if agent.display_id == 0:
                adb.back()
            else:
                adb.back_display(agent.display_id)
            adb.settle(0.8)
            local_memory += "\nPrevious action: back. If the same screen remains, choose a different action."
            continue
        if name == "click":
            reporter.state_event(agent.name, "ACTING", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id, action=name)
            if "x" in action and "y" in action:
                x = int(action["x"])
                y = int(action["y"])
            elif isinstance(action.get("point"), list) and len(action["point"]) == 2:
                x = int(action["point"][0])
                y = int(action["point"][1])
            else:
                local_memory += f"\nInvalid click action lacked coordinates: {action}. Return click with x and y."
                continue
            original = (x, y)
            x, y, snap_reason = _snap_to_button_center(screenshot, x, y)
            if (x, y) != original:
                reporter.event("coordinate_snap", runtime="vlm_action_demo", agent=agent.name, phase=phase, from_xy=original, to_xy=(x, y), reason=snap_reason)
            _tap(adb, agent, x, y)
            local_memory += f"\nPrevious action: clicked at ({x}, {y}). If the same screen remains, choose a different point, preferably the center of the intended button."
            continue
        local_memory += f"\nPrevious invalid action: {action}"
    reporter.state_event(agent.name, "FAILED", runtime="vlm_action_demo", phase=phase, display_id=agent.display_id, reason="max steps reached")
    return False, "max steps reached"


def _agents(adb: AdbClient) -> tuple[DemoAgent, DemoAgent]:
    displays = adb.list_displays()
    virtual = next((d for d in displays if d.display_id != 0 and d.surfaceflinger_id), None)
    if virtual is None:
        raise RuntimeError("no virtual display with SurfaceFlinger id is available")
    return (
        DemoAgent("shop_agent", "Mock Shop", "edu.agentos.mockshop", 0),
        DemoAgent("payment_agent", "Mock Payment", "edu.agentos.mockpayment", virtual.display_id, virtual.surfaceflinger_id),
    )


def run_demo(*, mode: str, run_root: Path) -> Path:
    adb = AdbClient()
    adb.require_device()
    adb.clear_app_data("edu.agentos.mockshop")
    adb.clear_app_data("edu.agentos.mockpayment")
    shop, payment = _agents(adb)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / f"shop_payment_authorization_{mode}_vlm_action_{timestamp}"
    reporter = RunReporter(run_dir)
    reporter.event("runtime_start", runtime=mode, task="shop_payment_authorization", execution_backend="direct_vlm_action_loop")
    client = GeminiScreenClient()
    shop_prepare = (
        "Open Mock Shop. Inspect order PX-1042. If the screen shows the order is awaiting payment authorization, complete this preparation phase. "
        "Do not open Mock Payment. Do not click COMPLETE AFTER PAYMENT."
    )
    payment_task = "Open Mock Payment. Approve payment for order PX-1042. Complete only when approved status is visible."
    shop_resume = (
        "Open Mock Shop. The peer payment agent reports that payment for PX-1042 was approved. "
        "Complete the order and finish only when the order status shows ready for pickup."
    )

    if mode == "steward_serial":
        ok1, msg1 = run_action_loop(adb=adb, client=client, reporter=reporter, agent=shop, instruction=shop_prepare, run_dir=run_dir, phase="prepare")
        ok2, msg2 = run_action_loop(adb=adb, client=client, reporter=reporter, agent=payment, instruction=payment_task, run_dir=run_dir, phase="provider") if ok1 else (False, "shop prepare failed")
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(run_action_loop, adb=adb, client=client, reporter=reporter, agent=shop, instruction=shop_prepare, run_dir=run_dir, phase="prepare"): "shop",
                pool.submit(run_action_loop, adb=adb, client=client, reporter=reporter, agent=payment, instruction=payment_task, run_dir=run_dir, phase="provider"): "payment",
            }
            results = {name: future.result() for future, name in ((future, futures[future]) for future in as_completed(futures))}
        ok1, msg1 = results["shop"]
        ok2, msg2 = results["payment"]

    request_id = "operation_px1042_payment"
    reporter.ipc_event(
        request_id=request_id,
        message_kind="RuntimeOperationResponse",
        status="delivered" if ok2 else "failed",
        from_agent=payment.name,
        to_agent=shop.name,
        mode=mode,
        via="steward" if mode == "steward_serial" else "peer",
        request_summary="authorize payment for order PX-1042",
        response_summary=msg2,
        evidence=msg2,
    )
    ok3, msg3 = run_action_loop(adb=adb, client=client, reporter=reporter, agent=shop, instruction=shop_resume, run_dir=run_dir, phase="resume", memory=msg2) if ok1 and ok2 else (False, "dependency failed")
    success = ok1 and ok2 and ok3 and "ready for pickup" in msg3.lower()
    reporter.event("runtime_finish", runtime=mode, task="shop_payment_authorization", success=success, reason=msg3)
    reporter.write_summary(task="shop_payment_authorization", runtime=mode, success=success, run_error=None if success else msg3)
    write_timeline(run_root, [run_dir])
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["steward_serial", "agentos_parallel"], required=True)
    parser.add_argument("--run-root", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument("--gemini-model", default="")
    args = parser.parse_args()
    if args.gemini_model:
        os.environ["GEMINI_MODEL"] = args.gemini_model
    print(run_demo(mode=args.mode, run_root=Path(args.run_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
