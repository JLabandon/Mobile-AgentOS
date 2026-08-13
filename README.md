# Mobile AgentOS Runtime

This repository contains a prototype runtime for app-oriented mobile agents on Android.

The current project focus is **Mobile AgentOS**: a scheduler-driven runtime where multiple app-bound agents share one device, exchange runtime information, and produce traceable execution evidence.

## Current Architecture

- `steward_serial`: MobileSteward-style baseline. The Steward plans app-level subtasks, runs them serially, and forwards upstream information to downstream agents.
- `multidisplay_split_phase`: Mobile AgentOS runtime. The Steward still performs upfront app-level planning, while the runtime owns scheduling, state transitions, resource records, IPC delivery, stale-action guards, and timeline output.
- `AppStaffAgent`: a shared app agent class. Each agent observes UI XML, sends the current UI and task context to the model, receives one JSON action, and executes only primitive UI actions.

AppAgents use the same action interface across apps:

```text
click
input
swipe
back
FINISH
REQUEST_INFORMATION
RESPOND_INFORMATION
REQUEST_OPERATION
RESPOND_OPERATION
```

The runtime code does not contain app-specific field adapters or semantic slot executors. App-specific knowledge belongs in app profiles, task fixtures, visible UI text, model prompts, and long-term memory.

## Benchmark Scope

The curated benchmark suite is in:

```text
benchmarks/tasks/curated_core.json
```

Representative tasks include:

- `calendar_keep_info`: Calendar uses information from Google Keep.
- `clock_keep_wakeup`: Google Clock uses time information from Google Keep.
- `calendar_gmail_meeting_detail`: Calendar uses meeting details from Gmail.
- `calendar_maps_place_check`: Calendar uses place evidence from Google Maps.
- `shop_payment_authorization`: Mock Shop uses a payment result from Mock Payment.

Task fixtures and controlled mock apps live under:

```text
benchmarks/fixtures
```

App profiles live under:

```text
config/apps.json
```

## Running

Create a `.env` file with `DEEPSEEK_API_KEY`, or keep using the existing sibling `.env` from the older prototype directory.

```bash
./run_mobile_agent_os.sh --runtime multidisplay_split_phase --task calendar_gmail_meeting_detail
./run_mobile_agent_os.sh --runtime steward_serial --task calendar_gmail_meeting_detail
./run_mobile_agent_os.sh --runtimes steward_serial,multidisplay_split_phase --task-suite curated_core
```

If no runtime is provided, the benchmark runner uses `multidisplay_split_phase`.

## Outputs

Each run writes:

- `trace.jsonl`
- `ipc_ledger.jsonl`
- `state_timeline.jsonl`
- `metrics.json`
- `summary.md`

A benchmark suite also writes:

- `comparison.md`
- `timeline.html`

## Notes

Real Android VirtualDisplay execution is partially supported by platform tools, but reliable per-display UI XML observation is not available through plain `uiautomator dump`. The current real-app benchmark therefore uses a foreground observation lane while preserving split-phase model thinking, scheduling, resource records, and IPC traceability.
