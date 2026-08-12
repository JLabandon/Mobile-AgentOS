from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


STATE_COLORS = {
    "READY": "#8fb996",
    "RUNNING": "#f2b84b",
    "WAIT_PEER": "#7aa2f7",
    "WAIT_EXTERNAL": "#b48ead",
    "WAIT_OBSERVATION": "#88c0d0",
    "WAIT_IME": "#d08770",
    "DONE": "#6cc070",
    "FAILED": "#d85f5f",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_timeline(run_root: Path, run_dirs: list[Path]) -> Path:
    cards: list[str] = []
    for run_dir in run_dirs:
        states = read_jsonl(run_dir / "state_timeline.jsonl")
        ipc = read_jsonl(run_dir / "ipc_ledger.jsonl")
        metrics_path = run_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        cards.append(_render_run(run_dir, states, ipc, metrics))
    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mobile AgentOS Timeline</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #202124; }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 18px; margin-top: 28px; }}
    .run {{ border: 1px solid #dadce0; border-radius: 6px; padding: 16px; margin-bottom: 20px; }}
    .agent {{ margin: 12px 0; }}
    .row {{ display: flex; align-items: center; gap: 8px; }}
    .label {{ width: 150px; font-weight: 600; }}
    .track {{ flex: 1; display: flex; height: 28px; border: 1px solid #e0e0e0; background: #fafafa; }}
    .seg {{ min-width: 8px; font-size: 11px; line-height: 28px; text-align: center; overflow: hidden; white-space: nowrap; color: #111; }}
    .ipc {{ margin: 8px 0; padding: 8px; background: #f8fafd; border-left: 3px solid #7aa2f7; }}
    code {{ background: #f1f3f4; padding: 1px 4px; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>Mobile AgentOS Timeline</h1>
  {''.join(cards)}
</body>
</html>
"""
    path = run_root / "timeline.html"
    path.write_text(doc, encoding="utf-8")
    return path


def _render_run(run_dir: Path, states: list[dict[str, Any]], ipc: list[dict[str, Any]], metrics: dict[str, Any]) -> str:
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for event in states:
        by_agent.setdefault(str(event.get("agent")), []).append(event)
    max_t = max([float(event.get("t", 0.0)) for event in states] + [1.0])
    rows = []
    for agent, events in sorted(by_agent.items()):
        pieces = []
        for idx, event in enumerate(events):
            start = float(event.get("t", 0.0))
            end = float(events[idx + 1].get("t", max_t)) if idx + 1 < len(events) else max_t
            width = max(2.0, (end - start) / max_t * 100)
            state = str(event.get("state", "READY"))
            color = STATE_COLORS.get(state, "#c7c7c7")
            pieces.append(
                f'<div class="seg" title="{html.escape(json.dumps(event, ensure_ascii=False))}" style="width:{width:.2f}%;background:{color}">{html.escape(state)}</div>'
            )
        rows.append(f'<div class="agent"><div class="row"><div class="label">{html.escape(agent)}</div><div class="track">{"".join(pieces)}</div></div></div>')
    ipc_rows = [
        '<div class="ipc"><code>{}</code> {} -> {}: {} {}</div>'.format(
            html.escape(str(item.get("status"))),
            html.escape(str(item.get("from_agent"))),
            html.escape(str(item.get("to_agent"))),
            html.escape(str(item.get("message_kind"))),
            html.escape(str(item.get("request_summary") or item.get("response_summary") or "")),
        )
        for item in ipc
    ]
    title = f"{metrics.get('task', run_dir.name)} / {metrics.get('runtime', '')}"
    return f"""<section class="run">
  <h2>{html.escape(title)}</h2>
  <p>success=<code>{metrics.get('success')}</code>, time=<code>{metrics.get('wall_clock_time')}</code>, LLM=<code>{metrics.get('llm_calls')}</code>, IPC=<code>{metrics.get('ipc_messages')}</code>, WAIT_PEER=<code>{metrics.get('wait_peer_time')}</code></p>
  {''.join(rows)}
  <h2>IPC</h2>
  {''.join(ipc_rows) or '<p>No IPC events.</p>'}
</section>"""
