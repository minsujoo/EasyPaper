#!/bin/bash
# EasyPaper 데이터 백업 스크립트 (macOS / Linux)
# DB(easypaper.db) + library/ + uploads/를 타임스탬프가 찍힌 하나의
# 압축파일로 backups/ 디렉터리에 저장하고, 오래된 백업은 자동으로 정리합니다.
# cache/는 재생성 가능한 파생 데이터라 백업 대상에서 제외합니다.

set -e

# 저장소 루트 기준으로 동작하도록 이동 (이 스크립트는 scripts/sh/ 하위에 있음)
cd "$(dirname "$0")/../.."

BACKUP_DIR="backups"
KEEP_COUNT="${EASYPAPER_BACKUP_KEEP:-10}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_NAME="easypaper_backup_${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "📦 EasyPaper 데이터 백업 시작..."

TARGETS=()
[ -f "backend/easypaper.db" ] && TARGETS+=("backend/easypaper.db")
[ -d "backend/library" ] && TARGETS+=("backend/library")
[ -d "backend/uploads" ] && TARGETS+=("backend/uploads")

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "⚠️  백업할 데이터(DB/library/uploads)가 없습니다. 아직 실행되지 않은 설치일 수 있습니다."
    exit 0
fi

tar -czf "${BACKUP_DIR}/${ARCHIVE_NAME}" "${TARGETS[@]}"

BACKUP_SIZE=$(du -h "${BACKUP_DIR}/${ARCHIVE_NAME}" | cut -f1)
echo "✅ 백업 완료: ${BACKUP_DIR}/${ARCHIVE_NAME} (${BACKUP_SIZE})"

# 오래된 백업 정리 (최신 KEEP_COUNT개만 유지)
BACKUP_COUNT=$(ls -1 "${BACKUP_DIR}"/easypaper_backup_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt "$KEEP_COUNT" ]; then
    TO_DELETE=$((BACKUP_COUNT - KEEP_COUNT))
    echo "🧹 백업 개수(${BACKUP_COUNT})가 보관 한도(${KEEP_COUNT})를 초과해 오래된 백업 ${TO_DELETE}개를 정리합니다..."
    ls -1t "${BACKUP_DIR}"/easypaper_backup_*.tar.gz | tail -n "$TO_DELETE" | xargs rm -f
fi

echo "📂 현재 보관 중인 백업 목록:"
ls -1t "${BACKUP_DIR}"/easypaper_backup_*.tar.gz 2>/dev/null | head -n "$KEEP_COUNT"
