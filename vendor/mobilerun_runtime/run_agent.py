from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


VENDOR_ROOT = Path(__file__).resolve().parent
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _event_payload(event: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": event.__class__.__name__}
    data = getattr(event, "__dict__", None)
    if isinstance(data, dict):
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if key in {"screenshot", "image"} or isinstance(value, (bytes, bytearray)):
                payload[key] = f"<{type(value).__name__}:{len(value)} bytes>"
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            else:
                payload[key] = str(value)
    return payload


async def _run_agent(args: argparse.Namespace) -> dict[str, Any]:
    from mobilerun import MobileAgent, MobileConfig
    from mobilerun.agent.utils.llm_picker import load_llm

    config = MobileConfig()
    config.agent.max_steps = args.max_steps
    config.agent.reasoning = args.reasoning
    config.agent.streaming = False
    config.agent.vision_only = True
    config.agent.after_sleep_action = args.after_sleep_action
    config.agent.wait_for_stable_ui = args.wait_for_stable_ui
    config.device.serial = args.device
    config.device.portal_mode = args.portal_mode
    config.device.auto_setup = args.portal_mode != "disabled"
    config.logging.debug = args.debug
    config.logging.save_trajectory = "none"
    config.telemetry.enabled = False
    config.tracing.enabled = False

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required")
    os.environ.setdefault("GOOGLE_API_KEY", api_key)

    llm = load_llm(
        provider_name=args.provider,
        model=args.model,
        api_key=api_key,
        temperature=args.temperature,
    )
    agent = MobileAgent(goal=args.goal, config=config, llms=llm, timeout=args.timeout)
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    handler = agent.run()
    async for event in handler.stream_events():
        item = _event_payload(event)
        item["t"] = round(time.monotonic() - started, 3)
        events.append(item)
    result = await handler
    return {
        "success": bool(getattr(result, "success", False)),
        "reason": str(getattr(result, "reason", "")),
        "elapsed": round(time.monotonic() - started, 3),
        "events": events,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a vendored MobileRun MobileAgent.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--device")
    parser.add_argument("--provider", default="GoogleGenAI")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=1000)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--portal-mode", choices=["auto", "required", "disabled"], default="disabled")
    parser.add_argument("--after-sleep-action", type=float, default=1.0)
    parser.add_argument("--wait-for-stable-ui", type=float, default=0.3)
    parser.add_argument("--reasoning", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for env_file in args.env_file:
        _load_env_file(Path(env_file))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(_run_agent(args))
    except Exception as exc:
        result = {
            "success": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
            "elapsed": 0,
            "events": [],
        }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
