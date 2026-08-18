#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" - <<'PY'
try:
    import invoke
    import yaml
except Exception:
    raise SystemExit(1)
PY
then
  "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt" >/dev/null
fi

exec "$VENV_DIR/bin/invoke" run --config "$ROOT_DIR/config.yml" --operation archive_7z "$@"