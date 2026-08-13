#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -q -r requirements.txt

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
elif [ -f "../agent_ipc_mvp/.env" ]; then
  set -a
  source ../agent_ipc_mvp/.env
  set +a
fi

if [ -z "${ADB:-}" ] && [ -x "$HOME/Library/Android/sdk/platform-tools/adb" ]; then
  export ADB="$HOME/Library/Android/sdk/platform-tools/adb"
fi

python -m mobile_agent_os.benchmark.run_benchmark "$@"
