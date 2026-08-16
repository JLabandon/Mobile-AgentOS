# Mobile AgentOS Runtime

This repository contains a prototype runtime for app-oriented mobile agents on Android.

The current project focus is **Mobile AgentOS**: a scheduler-driven runtime where multiple app-bound agents share one device, exchange runtime information, and produce traceable execution evidence.

## Current Architecture

Mobile AgentOS currently has two layers of runtime work:

- Legacy XML/DeepSeek runtimes remain available for earlier benchmarks: `steward_serial` and `agentos_parallel`.
- Stage 5 adds a VLM-backed job scheduler path: `job_level_steward_serial` and `job_level_agentos`.

The Stage 5 path decomposes each app-agent loop into schedulable jobs:

```text
ObservationJob -> ThinkingJob -> ActionJob -> SettleWaitJob -> IPCDeliveryJob
```

`job_level_steward_serial` uses the same executor and task config as AgentOS, while enforcing a configured serial order. `job_level_agentos` runs the FIFO scheduler with concurrent workers, dependency checks, structured IPC, resident app reuse, and traceable runtime state.

The current scheduler is intentionally simple: FIFO, no priority, no preemption, and a lightweight resource capacity table. Complex resource arbitration and larger task suites are planned for Stage 6.

Project documents:

```text
docs/08_stage5_report.md
docs/09_stage6_plan.md
```

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

Create a `.env` file with `DEEPSEEK_API_KEY` for the legacy XML/DeepSeek path. The Stage 5 VLM path uses `GEMINI_API_KEY` or `GOOGLE_API_KEY`.

Legacy benchmark examples:

```bash
./run_mobile_agent_os.sh --runtime agentos_parallel --task calendar_gmail_meeting_detail
./run_mobile_agent_os.sh --runtime steward_serial --task calendar_gmail_meeting_detail
./run_mobile_agent_os.sh --runtimes steward_serial,agentos_parallel --task-suite curated_core
```

Stage 5 scheduler examples:

```bash
python -m mobile_agent_os.benchmark.run_job_level_demo --mode job_level_steward_serial --task shop_payment_authorization
python -m mobile_agent_os.benchmark.run_job_level_demo --mode job_level_agentos --task shop_payment_authorization
```

If no runtime is provided to `run_mobile_agent_os.sh`, the legacy benchmark runner uses `agentos_parallel`.

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
