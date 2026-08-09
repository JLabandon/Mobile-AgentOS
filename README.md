# Agent IPC MVP

Minimal local controller for a MobileSteward-style baseline.

This project runs on the desktop side. Android Studio provides the emulator, ADB provides observation/action, and DeepSeek decides the next UI action from the current UI XML. The first version is deliberately centralized:

```text
StewardAgent -> CalendarAgent -> ClockAgent
```

There is no peer-to-peer agent communication yet.

## Requirements

- Android Studio emulator with ADB available.
- Google Calendar installed as `com.google.android.calendar`.
- System Clock installed as `com.google.android.deskclock` or `com.android.deskclock`.
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

## Run

```bash
python -m agent_ipc_mvp.run_demo --task calendar_clock_draft
```

The command prints the generated run summary path. Each run writes:

- `trace.jsonl`
- per-step `window_dump.xml`
- per-step `screenshot.png`
- `summary.md`

## Current Limits

- Fixed task only: create Calendar and Clock drafts.
- XML/text-only model input; screenshots are saved for human inspection.
- Draft-only mode: the model is instructed not to tap final Save/Create/Done controls.
- Emulator-first; real devices may work but are not the target for this MVP.
