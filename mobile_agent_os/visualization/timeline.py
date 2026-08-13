from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from datetime import datetime


STATE_COLORS = {
    "READY": "#86a873",
    "OBSERVING": "#4f8fc0",
    "THINKING": "#d89a2b",
    "READY_TO_ACT": "#8b6fc6",
    "ACTING": "#c75c5c",
    "WAIT_PEER": "#5277d8",
    "WAIT_RESOURCE": "#b96b45",
    "DONE": "#3f9b59",
    "FAILED": "#c43d3d",
}

KEY_EVENTS = {
    "steward_plan",
    "display_slot_allocated",
    "app_launch",
    "app_resume",
    "display_observe",
    "llm_submitted",
    "llm_completed",
    "ready_to_act",
    "action_guard",
    "peer_result_delivered",
    "agent_step",
    "runtime_finish",
    "hidden_evaluation",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_timeline(run_root: Path, run_dirs: list[Path]) -> Path:
    runs = []
    for run_dir in run_dirs:
        trace = read_jsonl(run_dir / "trace.jsonl")
        states = read_jsonl(run_dir / "state_timeline.jsonl")
        _attach_relative_times(trace, states)
        runs.append(
            {
                "name": run_dir.name,
                "states": states,
                "ipc": read_jsonl(run_dir / "ipc_ledger.jsonl"),
                "trace": trace,
                "metrics": _read_json(run_dir / "metrics.json"),
            }
        )
    data = json.dumps(runs, ensure_ascii=False, default=str)
    path = run_root / "timeline.html"
    path.write_text(_html(data), encoding="utf-8")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _attach_relative_times(trace: list[dict[str, Any]], states: list[dict[str, Any]]) -> None:
    if not trace:
        return
    base = _parse_time(str(trace[0].get("time", "")))
    if not base:
        return
    for event in trace:
        if "t" in event:
            continue
        current = _parse_time(str(event.get("time", "")))
        if current:
            event["t"] = round((current - base).total_seconds(), 3)
    first_state_t = min((float(event.get("t", 0.0)) for event in states), default=0.0)
    if first_state_t:
        for event in states:
            event["t"] = round(float(event.get("t", 0.0)) - first_state_t, 3)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _html(data: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mobile AgentOS Timeline</title>
  <style>
    :root {{
      --text: #202124;
      --muted: #5f6368;
      --line: #dadce0;
      --soft: #f8fafd;
      --warn: #fff7df;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: #fff;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: #fff;
      border-bottom: 1px solid var(--line);
      padding: 14px 20px;
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    h1 {{ font-size: 20px; margin: 0; }}
    select, input {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      font: inherit;
      background: #fff;
    }}
    main {{ padding: 18px 20px 32px; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 8px;
      margin-bottom: 18px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fff;
    }}
    .metric b {{ display: block; font-size: 18px; margin-top: 4px; }}
    .metric span {{ color: var(--muted); font-size: 12px; }}
    .section {{
      border: 1px solid var(--line);
      border-radius: 6px;
      margin: 14px 0;
      overflow: hidden;
    }}
    .section h2 {{
      font-size: 15px;
      margin: 0;
      padding: 10px 12px;
      background: #f6f8fa;
      border-bottom: 1px solid var(--line);
    }}
    .lane {{ padding: 12px; }}
    .agent-row {{
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 10px;
      align-items: center;
      margin: 9px 0;
    }}
    .agent-name {{ font-weight: 600; overflow: hidden; text-overflow: ellipsis; }}
    .track {{
      position: relative;
      height: 34px;
      border: 1px solid #e6e6e6;
      background: linear-gradient(90deg, #fff, #fafafa);
      border-radius: 4px;
      overflow: hidden;
    }}
    .seg {{
      position: absolute;
      top: 0;
      height: 100%;
      min-width: 2px;
      color: #111;
      font-size: 11px;
      line-height: 34px;
      text-align: center;
      overflow: hidden;
      white-space: nowrap;
      border-right: 1px solid rgba(255,255,255,.65);
    }}
    .switch {{
      position: absolute;
      top: -1px;
      width: 2px;
      height: 36px;
      background: #111827;
      box-shadow: 0 0 0 2px rgba(255,255,255,.9);
      z-index: 1;
    }}
    .switch::after {{
      content: attr(data-label);
      position: absolute;
      top: -18px;
      left: 4px;
      color: #111827;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 1px 4px;
      font-size: 10px;
      white-space: nowrap;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      padding: 10px 12px;
      color: var(--muted);
      font-size: 12px;
    }}
    .chip {{ display: inline-flex; align-items: center; gap: 5px; }}
    .dot {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13px; }}
    th, td {{ text-align: left; vertical-align: top; padding: 8px 10px; border-bottom: 1px solid #edf0f2; overflow-wrap: anywhere; word-break: break-word; }}
    th {{ color: var(--muted); font-weight: 600; background: #fbfbfc; }}
    code {{ background: #f1f3f4; border-radius: 4px; padding: 1px 4px; }}
    .muted {{ color: var(--muted); }}
    .summary {{ max-width: 620px; white-space: normal; overflow-wrap: anywhere; word-break: break-word; }}
    details summary {{ cursor: pointer; color: #174ea6; }}
    pre {{ white-space: pre-wrap; max-height: 260px; overflow: auto; background: #f6f8fa; padding: 10px; border-radius: 6px; }}
  </style>
</head>
<body>
  <header>
    <h1>Mobile AgentOS Timeline</h1>
    <select id="runSelect"></select>
    <input id="filter" placeholder="Filter events or agents">
  </header>
  <main id="app"></main>
  <script>
    const runs = {data};
    const colors = {json.dumps(STATE_COLORS)};
    const keyEvents = new Set({json.dumps(sorted(KEY_EVENTS))});
    const select = document.getElementById('runSelect');
    const filter = document.getElementById('filter');
    const app = document.getElementById('app');

    for (const [idx, run] of runs.entries()) {{
      const opt = document.createElement('option');
      opt.value = idx;
      opt.textContent = `${{run.metrics.task || run.name}} / ${{run.metrics.runtime || ''}}`;
      select.appendChild(opt);
    }}
    select.addEventListener('change', render);
    filter.addEventListener('input', render);
    render();

    function render() {{
      const run = runs[Number(select.value || 0)] || runs[0];
      if (!run) {{
        app.innerHTML = '<p>No runs.</p>';
        return;
      }}
      const q = filter.value.trim().toLowerCase();
      app.innerHTML = metrics(run.metrics) + lanes(run.states, run.trace) + ipcTable(run.ipc, q) + eventTable(run.trace, q);
    }}

    function metrics(m) {{
      const items = [
        ['success', m.success],
        ['wall-clock', fmt(m.wall_clock_time, 's')],
        ['LLM calls', m.llm_calls],
        ['UI actions', m.adb_ui_actions],
        ['app switches', m.app_switches],
        ['IPC messages', m.ipc_messages],
        ['guard fail', m.fast_guard_fail_count],
        ['overlap', fmt(m.llm_overlap_time, 's')],
      ];
      return `<div class="metrics">${{items.map(([k,v]) => `<div class="metric"><span>${{esc(k)}}</span><b>${{esc(v)}}</b></div>`).join('')}}</div>`;
    }}

    function lanes(states, trace) {{
      const byAgent = new Map();
      let maxT = 1;
      for (const ev of states) {{
        if (!byAgent.has(ev.agent)) byAgent.set(ev.agent, []);
        byAgent.get(ev.agent).push(ev);
        maxT = Math.max(maxT, Number(ev.t || 0));
      }}
      for (const ev of trace) {{
        if ((ev.kind === 'app_launch' || ev.kind === 'app_resume') && ev.agent) {{
          maxT = Math.max(maxT, Number(ev.t || 0));
          if (!byAgent.has(ev.agent)) byAgent.set(ev.agent, []);
        }}
      }}
      let rows = '';
      for (const [agent, events] of [...byAgent.entries()].sort()) {{
        events.sort((a,b) => Number(a.t || 0) - Number(b.t || 0));
        const segs = events.map((ev, i) => {{
          const start = Number(ev.t || 0);
          const end = i + 1 < events.length ? Number(events[i + 1].t || maxT) : maxT;
          const left = Math.max(0, start / maxT * 100);
          const width = Math.max(0.6, (Math.max(start, end) - start) / maxT * 100);
          const color = colors[ev.state] || '#c7c7c7';
          const title = `${{ev.state}} ${{start.toFixed(2)}}s-${{end.toFixed(2)}}s\\n${{JSON.stringify(ev, null, 2)}}`;
          return `<div class="seg" title="${{esc(title)}}" style="left:${{left.toFixed(2)}}%;width:${{width.toFixed(2)}}%;background:${{color}}">${{esc(ev.state)}}</div>`;
        }}).join('');
        const switches = trace.filter(ev => (ev.kind === 'app_launch' || ev.kind === 'app_resume') && ev.agent === agent).map(ev => {{
          const left = Math.max(0, Number(ev.t || 0) / maxT * 100);
          const label = ev.kind === 'app_launch' ? 'launch' : 'resume';
          const title = `${{label}} @ ${{Number(ev.t || 0).toFixed(2)}}s\\n${{ev.package || ''}}`;
          return `<div class="switch" data-label="${{esc(label)}}" title="${{esc(title)}}" style="left:${{left.toFixed(2)}}%"></div>`;
        }}).join('');
        rows += `<div class="agent-row"><div class="agent-name">${{esc(agent)}}</div><div class="track">${{segs}}${{switches}}</div></div>`;
      }}
      const legend = Object.entries(colors).map(([state, color]) => `<span class="chip"><i class="dot" style="background:${{color}}"></i>${{esc(state)}}</span>`).join('');
      return `<section class="section"><h2>Agent State Lanes</h2><div class="legend">${{legend}}</div><div class="lane">${{rows || '<p class="muted">No state events.</p>'}}</div></section>`;
    }}

    function ipcTable(ipc, q) {{
      const rows = ipc.filter(ev => match(ev, q)).map(ev => `
        <tr>
          <td><code>${{esc(ev.status)}}</code></td>
          <td>${{esc(ev.from_agent)}} → ${{esc(ev.to_agent)}}</td>
          <td>${{esc(ev.via || '')}}</td>
          <td class="summary">${{esc(ev.request_summary || ev.response_summary || '')}}</td>
          <td>${{link(ev.payload_ref || ev.evidence_ref)}}</td>
        </tr>`).join('');
      return `<section class="section"><h2>IPC Ledger</h2><table><colgroup><col style="width:12%"><col style="width:22%"><col style="width:10%"><col style="width:38%"><col style="width:18%"></colgroup><thead><tr><th>Status</th><th>Route</th><th>Via</th><th>Content</th><th>Evidence</th></tr></thead><tbody>${{rows || '<tr><td colspan="5" class="muted">No IPC events.</td></tr>'}}</tbody></table></section>`;
    }}

    function eventTable(trace, q) {{
      const rows = trace.filter(ev => keyEvents.has(ev.kind)).filter(ev => match(ev, q)).map(ev => {{
        const brief = eventBrief(ev);
        return `<tr><td>${{esc(ev.time || '')}}</td><td><code>${{esc(ev.kind)}}</code></td><td>${{esc(ev.agent || ev.source_agent || '')}}</td><td class="summary">${{esc(brief)}}</td><td><details><summary>raw</summary><pre>${{esc(JSON.stringify(ev, null, 2))}}</pre></details></td></tr>`;
      }}).join('');
      return `<section class="section"><h2>Key Runtime Events</h2><table><colgroup><col style="width:18%"><col style="width:18%"><col style="width:16%"><col style="width:34%"><col style="width:14%"></colgroup><thead><tr><th>Time</th><th>Kind</th><th>Agent</th><th>Summary</th><th>Raw</th></tr></thead><tbody>${{rows || '<tr><td colspan="5" class="muted">No matching events.</td></tr>'}}</tbody></table></section>`;
    }}

    function eventBrief(ev) {{
      if (ev.kind === 'steward_plan') return ev.message || '';
      if (ev.kind === 'app_launch' || ev.kind === 'app_resume') return `${{ev.package || ''}} foreground=${{ev.foreground || ''}}`;
      if (ev.kind === 'display_observe') return `display=${{ev.display_id}} package=${{(ev.observed_packages || []).join(',')}}`;
      if (ev.kind === 'model_call') return `step=${{ev.step}}`;
      if (ev.kind === 'action_guard') return `${{ev.result}} mode=${{ev.mode}} age=${{ev.snapshot_age_ms}}ms`;
      if (ev.kind === 'agent_step') return `${{ev.status}} ${{(ev.action && ev.action.action) || ''}} ${{ev.reason || ''}}`;
      if (ev.kind === 'peer_result_delivered') return `${{ev.source_agent}} → ${{ev.target_agent}}`;
      if (ev.kind === 'hidden_evaluation') return ev.message || '';
      return JSON.stringify(ev);
    }}

    function match(ev, q) {{
      if (!q) return true;
      return JSON.stringify(ev).toLowerCase().includes(q);
    }}

    function fmt(v, suffix) {{
      if (v === undefined || v === null) return '';
      return `${{v}}${{suffix}}`;
    }}

    function link(path) {{
      if (!path) return '';
      return `<code>${{esc(path)}}</code>`;
    }}

    function esc(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}
  </script>
</body>
</html>
"""
