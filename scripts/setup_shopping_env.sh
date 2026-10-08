#!/usr/bin/env bash
# Install only the shopping project's declared development dependency closure.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_dir="$repo_root/python/.venv"
requirements="$repo_root/python/requirements-shopping-dev.txt"
lock="$repo_root/python/requirements-shopping-lock.txt"

is_supported_python() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in {(3, 11), (3, 12)} else 1)' 2>/dev/null
}

if [[ -n "${SHOPPING_PYTHON:-}" ]] && ! is_supported_python "$SHOPPING_PYTHON"; then
  printf '%s\n' 'ERROR: SHOPPING_PYTHON must be an executable Python 3.11 or 3.12.' >&2
  exit 1
fi

if [[ -e "$env_dir" ]]; then
  if [[ ! -x "$env_dir/bin/python" ]] || ! is_supported_python "$env_dir/bin/python"; then
    printf '%s\n' 'ERROR: existing python/.venv needs Python 3.11 or 3.12; preserved without replacement.' >&2
    exit 1
  fi
else
  chosen_python=""
  if [[ -n "${SHOPPING_PYTHON:-}" ]]; then
    chosen_python="$SHOPPING_PYTHON"
  else
    for candidate in python3.11 python3.12 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && is_supported_python "$candidate"; then
        chosen_python="$(command -v "$candidate")"
        break
      fi
    done
  fi
  if [[ -z "$chosen_python" ]]; then
    printf '%s\n' 'ERROR: Python 3.11 or 3.12 is required; no environment was created.' >&2
    exit 1
  fi
  "$chosen_python" -m venv "$env_dir"
fi

"$env_dir/bin/python" - <<'PY'
import pathlib, sys
if sys.prefix == sys.base_prefix:
    raise SystemExit('ERROR: expected an isolated virtual environment')
config = (pathlib.Path(sys.prefix) / 'pyvenv.cfg').read_text().lower()
if 'include-system-site-packages = false' not in config:
    raise SystemExit('ERROR: system site packages must be disabled')
PY

if [[ -f "$lock" ]]; then
  "$env_dir/bin/python" -m pip install -r "$requirements" -c "$lock"
else
  "$env_dir/bin/python" -m pip install -r "$requirements"
fi
"$env_dir/bin/python" -m pip check
"$env_dir/bin/python" - <<'PY'
import importlib, sys
for module in ('fastapi', 'uvicorn', 'pydantic', 'sqlalchemy', 'langgraph', 'langchain_openai', 'httpx', 'pytest'):
    importlib.import_module(module)
print(f'Shopping environment verified: Python {sys.version.split()[0]}; key imports OK')
print('Run from python/: .venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_ab_test.py')
PY
