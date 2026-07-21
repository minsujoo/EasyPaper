#!/bin/bash
# EasyPaper 데이터 복원 스크립트 (macOS / Linux)
# backup.sh로 만든 백업 압축파일에서 DB/library/uploads를 복원합니다.
# 실행 전 서비스를 중지해두는 것을 권장합니다 (백업 파일과 현재 데이터가
# 동시에 쓰기 충돌하지 않도록).

set -e

cd "$(dirname "$0")/../.."

if [ -z "$1" ]; then
    echo "사용법: $0 <백업파일.tar.gz>"
    echo ""
    echo "사용 가능한 백업 목록:"
    ls -1t backups/easypaper_backup_*.tar.gz 2>/dev/null || echo "  (backups/ 디렉터리에 백업이 없습니다)"
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ 백업 파일을 찾을 수 없습니다: $BACKUP_FILE"
    exit 1
fi

echo "⚠️  이 작업은 현재 backend/easypaper.db, backend/library/, backend/uploads/를"
echo "    백업 파일 시점의 데이터로 덮어씁니다."
read -p "계속하시겠습니까? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "취소되었습니다."
    exit 0
fi

echo "📦 복원 중: $BACKUP_FILE ..."
tar -xzf "$BACKUP_FILE" -C .

echo "✅ 복원 완료. 서비스를 재시작해주세요:"
echo "   sudo systemctl restart easypaper   (systemd로 등록한 경우)"
echo "   또는 scripts/sh/start.sh를 다시 실행"
