#!/bin/bash
# EasyPaper Production Server Start Script

set -e

echo "📡 EasyPaper 서버 시작 중..."

# Ensure we run from backend folder with .venv python
cd "$(dirname "$0")/backend"

if [ ! -d ".venv" ]; then
    echo "❌ Error: Python 가상환경이 존재하지 않습니다. 먼저 './setup.sh'를 실행해주세요."
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env 파일이 존재하지 않아 기본값으로 시작합니다."
fi

# venv 내부 경로는 OS에 따라 다름 (macOS/Linux: .venv/bin, Windows: .venv/Scripts)
if [ -f ".venv/bin/python" ]; then
    VENV_PYTHON=".venv/bin/python"
else
    VENV_PYTHON=".venv/Scripts/python.exe"
fi

# Run backend which also serves the compiled frontend dist
exec "$VENV_PYTHON" main.py
