# Mobile AgentOS Runtime

This repository contains a prototype runtime for app-oriented mobile agents on Android.

The current project focus is **Mobile AgentOS**: a scheduler-driven runtime where multiple app-bound agents share one device, exchange runtime information, and produce traceable execution evidence.

## Current Architecture

Mobile AgentOS currently has two runtime paths:

- XML/DeepSeek runtimes remain available for earlier real-app benchmark work: `steward_serial` and `agentos_parallel`.
- The current VLM-backed job scheduler path provides `job_level_steward_serial` and `job_level_agentos`.

The VLM scheduler decomposes each app-agent loop into schedulable jobs:

```text
ObservationJob -> ThinkingJob -> ActionJob -> SettleWaitJob -> IPCDeliveryJob
```

`job_level_steward_serial` uses the same executor and planner output as AgentOS while enforcing serial execution. `job_level_agentos` runs the FIFO scheduler with concurrent workers, dependency checks, structured IPC, resident app reuse, and traceable runtime state.

The current scheduler is intentionally simple: FIFO, no priority, no preemption, and non-reentrant AppAgent service queues for shared providers.

## Benchmark Scope

The core job-level benchmark suite is in:

```text
config/tasks/core_benchmark.json
```

Representative tasks include:

- `planned_shop_payment_authorization`: Mock Shop consumes a payment result from Mock Payment through planned IPC.
- `late_bound_appointment_location`: Mock Planner requests missing appointment information from Google Keep at runtime.
- `shared_provider_project_codes`: two requester apps contend for one Google Keep AppAgent service queue.

Task fixtures and controlled mock apps live under:

```text
benchmarks/fixtures
```

App profiles live under:

```text
config/apps.json
```

## Running

Create a `.env` file with `DEEPSEEK_API_KEY` for planning and the legacy XML/DeepSeek path. The VLM scheduler uses `GEMINI_API_KEY` or `GOOGLE_API_KEY`.

XML/DeepSeek benchmark examples:

```bash
./run_mobile_agent_os.sh --runtime agentos_parallel --task calendar_gmail_meeting_detail
./run_mobile_agent_os.sh --runtime steward_serial --task calendar_gmail_meeting_detail
./run_mobile_agent_os.sh --runtimes steward_serial,agentos_parallel --task-suite core_benchmark
```

VLM scheduler examples:

```bash
python -m mobile_agent_os.benchmark.run_job_level_demo --mode job_level_steward_serial --task planned_shop_payment_authorization
python -m mobile_agent_os.benchmark.run_job_level_demo --mode job_level_agentos --task late_bound_appointment_location
python -m mobile_agent_os.benchmark.run_job_level_demo --mode job_level_agentos --task shared_provider_project_codes
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

Real Android multi-display execution uses task-hosting display slots exposed by the device or emulator. These slots can host Android activities, accept display-scoped input, and provide per-display screenshots. The runtime keeps display provisioning behind the Android substrate layer, while scheduling, IPC, registry state, and AppAgent lifecycle remain independent of the specific device setup.
