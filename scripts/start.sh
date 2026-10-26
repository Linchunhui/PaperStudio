#!/usr/bin/env bash
set -euo pipefail
paperstudio_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$paperstudio_root"
if [[ -n "${PAPERSTUDIO_PYTHON:-}" ]]; then
  paperstudio_python="$PAPERSTUDIO_PYTHON"
elif [[ -x "$paperstudio_root/.venv/bin/python" ]]; then
  paperstudio_python="$paperstudio_root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  paperstudio_python="$(command -v python3)"
else
  printf '%s\n' 'Python 3.12 is required. Create .venv and install requirements.txt first.' >&2
  exit 1
fi
umask 077
exec "$paperstudio_python" "$paperstudio_root/run.py" "$@"
