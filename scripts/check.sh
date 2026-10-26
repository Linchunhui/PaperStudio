#!/usr/bin/env bash
set -euo pipefail
paperstudio_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$paperstudio_root"
if [[ -n "${PAPERSTUDIO_PYTHON:-}" ]]; then
  paperstudio_python="$PAPERSTUDIO_PYTHON"
elif [[ -x "$paperstudio_root/.venv/bin/python" ]]; then
  paperstudio_python="$paperstudio_root/.venv/bin/python"
else
  paperstudio_python="$(command -v python3)"
fi
command -v node >/dev/null 2>&1 || { printf '%s\n' 'Node.js is required for frontend regression checks.' >&2; exit 1; }
paperstudio_test_data="$(mktemp -d "${TMPDIR:-/tmp}/paperstudio-check.XXXXXX")"
trap 'rm -rf -- "$paperstudio_test_data"' EXIT
export PAPERSTUDIO_DATA_DIR="$paperstudio_test_data"
"$paperstudio_python" -m compileall -q app run.py scripts
"$paperstudio_python" -m pytest -q "$@"
for paperstudio_module in web/*.js; do
  node --check --input-type=module < "$paperstudio_module"
done
for paperstudio_test in tests/*.mjs; do
  node "$paperstudio_test"
done
