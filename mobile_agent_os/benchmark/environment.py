from __future__ import annotations

import json
import os
from pathlib import Path

from ..model_clients.deepseek import DeepSeekClient
from ..report import RunReporter


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_deepseek_smoke(llm: DeepSeekClient, reporter: RunReporter, run_dir: Path) -> None:
    system = "Return json only. Do not include markdown or explanation."
    user = 'Return exactly this json object: {"ok": true}'
    prompt_path = run_dir / "deepseek_smoke_prompt.json"
    response_path = run_dir / "deepseek_smoke_response.txt"
    prompt_path.write_text(json.dumps({"system": system, "user": user}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    raw_content = llm.raw_chat(system=system, user=user, max_tokens=50)
    response_path.write_text(raw_content, encoding="utf-8", errors="replace")
    reporter.event("model_call", agent="deepseek_smoke", step=0, attempt=1, prompt=str(prompt_path), response=str(response_path), raw_response=raw_content)
    reporter.event("environment", message=f"DeepSeek smoke: {llm.parse_json_content(raw_content)}")
