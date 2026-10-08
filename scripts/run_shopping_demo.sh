#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$repo_root/python/.venv/bin/python" ]]; then
  printf '%s\n' 'Run ./scripts/setup_shopping_env.sh first (Python 3.11/3.12).' >&2
  exit 1
fi
cd -- "$repo_root"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$repo_root/python${PYTHONPATH:+:$PYTHONPATH}"
exec "$repo_root/python/.venv/bin/python" -m shopping_agent.demo "$@"
