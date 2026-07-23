#!/usr/bin/env bash
# backend/packaging/build.sh(.bat)로 만든 PyInstaller onedir 산출물을
# src-tauri/binaries/easypaper-backend/로 복사한다.
#
# 참고: PyInstaller onedir은 실행파일 하나가 아니라 지원 파일(_internal/)이
# 딸린 디렉토리라, Tauri의 externalBin(sidecar) 규칙(단일 파일,
# "<name>-<target-triple>" 명명)에 맞지 않는다. 그래서 tauri.conf.json의
# bundle.resources로 디렉토리 전체를 번들에 포함시키고, src-tauri/src/lib.rs가
# 런타임에 직접 std::process::Command로 그 안의 실행파일을 실행하는 방식을 쓴다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_TAURI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SRC_TAURI_DIR/.." && pwd)"

BUILD_OUTPUT="$REPO_ROOT/backend/packaging/dist/easypaper-backend"
DEST="$SRC_TAURI_DIR/binaries/easypaper-backend"

if [ ! -d "$BUILD_OUTPUT" ]; then
  echo "빌드 산출물이 없습니다: $BUILD_OUTPUT (먼저 backend/packaging/build.sh를 실행하세요)" >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$SRC_TAURI_DIR/binaries"
cp -r "$BUILD_OUTPUT" "$DEST"

echo "Copied: $BUILD_OUTPUT -> $DEST"
