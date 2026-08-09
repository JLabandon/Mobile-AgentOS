# Agent IPC MVP

Minimal local controller for a MobileSteward-style baseline and Agent IPC experiments.

This project runs on the desktop side. Android Studio provides the emulator, ADB provides observation/action, and DeepSeek decides the next UI action from the current UI XML.

```text
StewardAgent -> AppStaffAgent(Calendar) -> AppStaffAgent(Clock)
```

It also includes a Runtime Information Request task:

```text
AppStaffAgent(Calendar) <-> AppStaffAgent(Google Keep)
```

All app agents are instances of the same `AppStaffAgent` class. App-specific behavior comes from `config/apps.json` app profiles, task guideline memory, semantic slot adapters, and `config/tasks.json` task assignments.

## Requirements

- Android Studio emulator with ADB available.
- Google Calendar installed as `com.google.android.calendar`.
- System Clock installed as `com.google.android.deskclock` or `com.android.deskclock`.
- Google Keep installed as `com.google.android.keep`.
- DeepSeek API key in `DEEPSEEK_API_KEY`.

Optional environment variables:

```bash
export ADB="$HOME/Library/Android/sdk/platform-tools/adb"
export ANDROID_SERIAL="emulator-5554"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-v4-flash"
```

## Install

```bash
cd projects/agent_ipc_mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For repeated local use, create a private `.env` file:

```bash
cp .env.example .env
```

Fill `DEEPSEEK_API_KEY` in `.env`. The file is ignored by git.

## Run

```bash
python -m agent_ipc_mvp.run_demo
```

Or use the convenience script, which creates `.venv`, installs dependencies,
loads `.env`, and runs the demo:

```bash
./run_mvp.sh
```

Supported tasks:

- `calendar_clock`: complete the Calendar event and Clock alarm flow.
- `calendar_keep_info`: create a Calendar event using runtime information read from Google Keep.

Supported communication modes:

- `steward`: Calendar request and Keep response are routed through Steward.
- `peer`: Calendar and Keep exchange the runtime request/response directly at the protocol level.

Examples:

```bash
./run_mvp.sh --task calendar_clock
./run_mvp.sh --task calendar_keep_info --mode steward
./run_mvp.sh --task calendar_keep_info --mode peer
```

The command prints the generated run summary path. Each run writes:

- `trace.jsonl`
- per-step `window_dump.xml`
- per-step `screenshot.png`
- `summary.md`

## Current Limits

- Fixed demo tasks only.
- XML/text-only model input; screenshots are saved for human inspection.
- Peer mode still shares one desktop-side Python control loop and one ADB client; it is not true multi-process IPC yet.
- Semantic slot adapters are still local execution helpers. The agent chooses when and how to use them; the adapter only performs the grounded app action.
- Emulator-first; real devices may work but are not the target for this MVP.
