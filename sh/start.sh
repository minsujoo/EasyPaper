#!/bin/bash
# EasyPaper Production Server Start Script

set -e

echo "📡 EasyPaper 서버 시작 중..."

# Ensure we run from backend folder with .venv python (이 스크립트는 sh/ 하위에 있음)
cd "$(dirname "$0")/../backend"

if [ ! -d ".venv" ]; then
    echo "❌ Error: Python 가상환경이 존재하지 않습니다. 먼저 './sh/setup.sh'를 실행해주세요."
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env 파일이 존재하지 않아 기본값으로 시작합니다."
fi

# Run backend which also serves the compiled frontend dist
exec .venv/bin/python main.py
