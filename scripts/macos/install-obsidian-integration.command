#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
plugin_source="${script_dir}/plugin"
engine_source="${script_dir}/engine"
plugin_id="paper-research-workspace"
data_dir="${HOME}/Library/Application Support/paper-research-workspace"
engine_target="${data_dir}/engine"

if [[ ! -f "${plugin_source}/manifest.json" || ! -x "${engine_source}/easypaper-backend" ]]; then
  echo "설치 묶음이 완전하지 않습니다. ZIP을 다시 풀고 이 파일을 실행해주세요." >&2
  exit 1
fi

vault_path="${1:-}"
if [[ -z "${vault_path}" ]]; then
  vault_path="$(osascript -e 'POSIX path of (choose folder with prompt "동기화할 Obsidian Vault 폴더를 선택하세요")')"
fi
vault_path="${vault_path%/}"
if [[ ! -d "${vault_path}/.obsidian" ]]; then
  echo "선택한 폴더에 .obsidian 디렉터리가 없습니다: ${vault_path}" >&2
  exit 1
fi

env_file="${data_dir}/.env"
connection_file="${2:-${HOME}/Downloads/paper-sync-connection.env}"
connection_source=""
sync_token=""
if [[ -f "${connection_file}" ]]; then
  sync_token="$(sed -n 's/^SYNC_TOKEN=//p' "${connection_file}" | head -n 1)"
  connection_source="${connection_file}"
elif [[ -f "${env_file}" ]]; then
  # Update installs reuse the token already protected in the user's data
  # directory, so a deleted one-time Taildrop file is not needed again.
  sync_token="$(sed -n 's/^SYNC_TOKEN=//p' "${env_file}" | head -n 1)"
fi
if [[ -z "${sync_token}" ]]; then
  connection_file="$(osascript -e 'POSIX path of (choose file with prompt "Tailscale로 받은 paper-sync-connection.env 파일을 선택하세요")')"
  sync_token="$(sed -n 's/^SYNC_TOKEN=//p' "${connection_file}" | head -n 1)"
  connection_source="${connection_file}"
fi
if [[ -z "${sync_token}" ]]; then
  echo "기존 설정이나 연결 설정 파일에서 SYNC_TOKEN을 찾지 못했습니다." >&2
  exit 1
fi

plugin_target="${vault_path}/.obsidian/plugins/${plugin_id}"
mkdir -p "${plugin_target}" "${data_dir}"
install -m 0644 "${plugin_source}/main.js" "${plugin_target}/main.js"
install -m 0644 "${plugin_source}/manifest.json" "${plugin_target}/manifest.json"
install -m 0644 "${plugin_source}/styles.css" "${plugin_target}/styles.css"
mkdir -p "${plugin_target}/pdfjs"
install -m 0644 "${plugin_source}/pdfjs/pdf.js" "${plugin_target}/pdfjs/pdf.js"
install -m 0644 "${plugin_source}/pdfjs/pdf.worker.js" "${plugin_target}/pdfjs/pdf.worker.js"

mkdir -p "${engine_target}"
/usr/bin/ditto "${engine_source}" "${engine_target}"
chmod 0755 "${engine_target}/easypaper-backend"
if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "${plugin_target}" "${engine_target}" 2>/dev/null || true
fi

touch "${env_file}"
chmod 0600 "${env_file}"

set_env_value() {
  env_key="$1"
  env_value="$2"
  temp_file="$(mktemp "${data_dir}/.env.XXXXXX")"
  awk -v key="${env_key}" 'index($0, key "=") != 1 { print }' "${env_file}" >"${temp_file}"
  printf '%s=%s\n' "${env_key}" "${env_value}" >>"${temp_file}"
  chmod 0600 "${temp_file}"
  mv -f "${temp_file}" "${env_file}"
}

set_env_value "SYNC_SERVER_URL" "https://rml.taileff9f6.ts.net"
set_env_value "SYNC_TOKEN" "${sync_token}"
set_env_value "SYNC_INTERVAL_SECONDS" "300"

echo
echo "설치가 완료됐습니다."
echo "1. Obsidian을 완전히 종료했다가 다시 실행하세요."
echo "2. 설정 > 커뮤니티 플러그인에서 Paper Research Workspace를 활성화하세요."
echo "3. 왼쪽 리본의 논문 연구 아이콘을 여세요."
if [[ -n "${connection_source}" ]]; then
  echo "4. 연결 확인 후 ${connection_source} 파일은 삭제해도 됩니다."
else
  echo "4. 기존 Mac 연결 설정을 그대로 재사용했습니다."
fi
echo
read -r -p "Enter를 누르면 창을 닫습니다. " _
