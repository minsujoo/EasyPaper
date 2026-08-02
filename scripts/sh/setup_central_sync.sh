#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
backend_dir="${project_root}/backend"
python_bin="${backend_dir}/.venv/bin/python"

if [[ ! -x "${python_bin}" ]]; then
  echo "백엔드 Python 환경을 찾을 수 없습니다: ${python_bin}" >&2
  exit 1
fi

config_root="${XDG_CONFIG_HOME:-${HOME}/.config}/paper-sync"
data_root="${XDG_DATA_HOME:-${HOME}/.local/share}/paper-sync"
app_data_root="${PAPER_APP_DATA_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/com.easypaper.desktop}"
unit_root="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
env_file="${config_root}/server.env"
unit_file="${unit_root}/paper-sync-server.service"

mkdir -p "${config_root}" "${data_root}/server/files" "${unit_root}" "${app_data_root}"
chmod 700 "${config_root}" "${data_root}" "${data_root}/server"

if [[ -f "${env_file}" ]]; then
  sync_token="$(sed -n 's/^SYNC_TOKEN=//p' "${env_file}" | head -n 1)"
fi
if [[ -z "${sync_token:-}" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    sync_token="$(openssl rand -hex 32)"
  else
    sync_token="$(${python_bin} -c 'import secrets; print(secrets.token_hex(32))')"
  fi
fi

umask 077
cat >"${env_file}" <<EOF
SYNC_TOKEN=${sync_token}
SYNC_HOST=127.0.0.1
SYNC_PORT=8766
SYNC_DB_PATH=${data_root}/server/sync.db
SYNC_STORAGE_DIR=${data_root}/server/files
SYNC_MAX_FILE_SIZE_MB=500
EOF
chmod 600 "${env_file}"

cat >"${unit_file}" <<EOF
[Unit]
Description=Personal Research Data Sync Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${backend_dir}
EnvironmentFile=${env_file}
ExecStart=${python_bin} ${backend_dir}/sync_server_app.py
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF

# Persist the loopback endpoint in the packaged desktop backend's private
# configuration.  The token value is never printed.
EASYPAPER_CONFIG_DIR="${app_data_root}" \
SYNC_BOOTSTRAP_TOKEN="${sync_token}" \
PYTHONPATH="${backend_dir}" \
"${python_bin}" - <<'PY'
import os
from config import update_sync_settings
update_sync_settings(
    "http://127.0.0.1:8766",
    os.environ["SYNC_BOOTSTRAP_TOKEN"],
    300,
)
PY

systemctl --user daemon-reload
systemctl --user enable --now paper-sync-server.service

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8766/v1/health >/dev/null 2>&1; then
    echo "중앙 동기화 서버가 준비되었습니다."
    echo "주소: http://127.0.0.1:8766"
    echo "토큰은 ${env_file}에 안전하게 저장했습니다."
    exit 0
  fi
  sleep 0.5
done

echo "서비스가 시작되지 않았습니다. 다음 명령으로 로그를 확인하세요:" >&2
echo "journalctl --user -u paper-sync-server.service -n 100 --no-pager" >&2
exit 1
