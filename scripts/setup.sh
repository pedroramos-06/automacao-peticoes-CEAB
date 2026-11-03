#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
venv_dir="${CEAB_VENV_DIR:-$project_dir/.venv}"
if [[ ! -x "$venv_dir/bin/python" ]]; then
  python3 -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install -r requirements.lock
# O robô usa o Chrome existente via CDP; Chromium do Playwright só é necessário
# para testes quando Chrome/Chromium não está instalado no sistema.
if ! command -v chromium >/dev/null && ! command -v google-chrome >/dev/null; then
  "$venv_dir/bin/python" -m playwright install chromium
fi
