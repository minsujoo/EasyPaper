#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
backend_dir="${project_root}/backend"
python_bin="${backend_dir}/.venv/bin/python"
app_data_root="${PAPER_APP_DATA_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/com.easypaper.desktop}"

if ! curl -fsS http://127.0.0.1:8766/v1/health >/dev/null; then
  echo "중앙 동기화 서버가 실행 중이 아닙니다." >&2
  exit 1
fi

EASYPAPER_CONFIG_DIR="${app_data_root}" \
DB_PATH="${app_data_root}/easypaper.db" \
LIBRARY_DIR="${app_data_root}/library" \
UPLOAD_DIR="${app_data_root}/uploads" \
CACHE_DIR="${app_data_root}/cache" \
PYTHONPATH="${backend_dir}" \
"${python_bin}" - <<'PY'
import json
from services.db import init_db
from services.sync_client import sync_once

init_db()
result = sync_once()
print(json.dumps(result, ensure_ascii=False))
PY
