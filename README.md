# Mobile AgentOS

Mobile AgentOS is a prototype runtime for coordinating multiple app-oriented agents on a single Android device.

The project studies a mobile operating-system layer for agents. A Steward agent plans work, app agents operate real apps through Android UI, and the runtime records scheduling, inter-agent communication, app switches, and benchmark evidence.

## Repository Branches

- `main`: Mobile AgentOS with the project files at the repository root.
- `mobile-agentos-main`: mirror branch for the Mobile AgentOS root project.
- `agent-ipc`: preserved Agent IPC prototype branch.

## Project Goals

- Run multiple app-bound agents on one Android emulator.
- Compare a MobileSteward-style serial baseline with runtime peer communication.
- Support runtime information requests and runtime operation requests between app agents.
- Keep every app agent on the same generic loop: observe UI, ask the model, execute a primitive action, record the result.
- Generate traces, IPC ledgers, state timelines, metrics, summaries, and HTML timeline views for analysis.

## Runtime Modes

- `steward_serial`: a centralized MobileSteward-style baseline. The Steward performs upfront scheduling and forwards information between app agents.
- `async_single_display`: a single-display runtime with mailbox-based peer information and operation requests.
- `resident_runtime`: a resident app-agent runtime with agent state, capability routing traces, mailbox lifecycle records, cooperative scheduling, and foreground interaction traces.

## Project Layout

```text
mobile_agent_os/                 Core runtime package
  agents.py                      Generic app agent loop
  steward.py                     Steward planning and routing
  runtime_requests.py            Information and operation request schemas
  ipc/                           Mailbox and IPC ledger
  runtimes/                      Runtime implementations
  benchmark/                     Benchmark runner and device preparation
  visualization/                 Timeline renderer
benchmarks/
  tasks/curated_core.json        Curated cross-app tasks
  fixtures/mock_apps/            Controlled mock apps for operation requests
config/apps.json                 App profiles and capabilities
memory/                          Long-term app-agent memory seeds
tests/                           Unit and fake-runtime tests
```

## Benchmark Tasks

The curated task suite covers representative cross-app patterns:

- `calendar_keep_info`: Calendar requests event information from Keep.
- `clock_keep_wakeup`: Clock requests wake-up time information from Keep.
- `calendar_gmail_meeting_detail`: Calendar requests meeting details from Gmail.
- `calendar_maps_place_check`: Calendar requests place evidence from Maps.
- `shop_payment_authorization`: Shop requests payment authorization from Payment.

Benchmark setup clears prior benchmark Calendar events by title and clears Clock alarms before each run. Calendar holiday events and account-synced events remain available as normal device state.

## Setup

Create a Python environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set the Android device and DeepSeek API variables:

```bash
export ADB="$HOME/Library/Android/sdk/platform-tools/adb"
export ANDROID_SERIAL=emulator-5554
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_MODEL=deepseek-v4-flash
```

The helper script loads `.env` from the repository root when the file exists.

## Run

Run one runtime on one task:

```bash
./run_mobile_agent_os.sh --runtime steward_serial --task calendar_keep_info
./run_mobile_agent_os.sh --runtime async_single_display --task calendar_keep_info
./run_mobile_agent_os.sh --runtime resident_runtime --task calendar_keep_info
```

Run a comparison:

```bash
./run_mobile_agent_os.sh \
  --runtimes steward_serial,resident_runtime \
  --task-suite curated_core
```

## Outputs

Each run writes:

- `trace.jsonl`
- `ipc_ledger.jsonl`
- `state_timeline.jsonl`
- `metrics.json`
- `summary.md`

Benchmark suites also write:

- `comparison.md`
- `timeline.html`

## Validation

Run static and unit checks:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/mobile_agent_os_pycache python -m compileall mobile_agent_os
python -m pytest
```

Run a device-preparation check from Python when a connected emulator is available:

```bash
python - <<'PY'
from pathlib import Path
from mobile_agent_os.adb import AdbClient
from mobile_agent_os.benchmark.device_prep import prepare_device
from mobile_agent_os.benchmark.loaders import load_app_configs
from mobile_agent_os.report import RunReporter

root = Path.cwd()
reporter = RunReporter(root / "runs" / "device_prep_cleanup_check")
configs = load_app_configs(root / "config" / "apps.json")
prepare_device(AdbClient(), configs, reporter)
print(reporter.run_dir / "trace.jsonl")
PY
```
