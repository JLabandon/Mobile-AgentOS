#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

pip install -r requirements.txt

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export ADB="${ADB:-/Users/luojingyu/Library/Android/sdk/platform-tools/adb}"
export ANDROID_SERIAL="${ANDROID_SERIAL:-emulator-5554}"
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
export DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-v4-flash}"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "Missing DEEPSEEK_API_KEY. Create projects/agent_ipc_mvp/.env from .env.example." >&2
  exit 2
fi

python -m agent_ipc_mvp.run_demo "$@"
