#!/bin/bash
# EasyPaper Setup & Installation Script

set -e

# 스크립트 위치 기준으로 동작하도록 이동 (어느 디렉터리에서 실행해도 안전)
cd "$(dirname "$0")"

# macOS/Linux 배포판에 따라 python3가 없고 python만 있는 경우를 대비
PYTHON_BIN="python3"
if ! command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

echo "========================================="
echo "⚙️  EasyPaper Auto Setup & Installation Script"
echo "========================================="

# 1. Setup Backend
echo "📡 1. Setting up Backend..."
cd backend
if [ ! -d ".venv" ]; then
    echo "   Creating Python virtual environment (.venv)..."
    "$PYTHON_BIN" -m venv .venv
fi
echo "   Installing backend dependencies..."
.venv/bin/pip install -r requirements.txt

if [ ! -f ".env" ]; then
    echo "   Creating configuration file (.env from template)..."
    cp .env.example .env
fi
cd ..

# 2. Setup Frontend
echo "🌐 2. Setting up Frontend..."
cd frontend
echo "   Installing frontend dependencies (npm install)..."
npm install
echo "   Building frontend production assets..."
npm run build
cd ..

echo "========================================="
echo "✅ EasyPaper Setup Complete!"
echo "========================================="
echo "To start the development servers concurrently, run:"
echo "   ./start-dev.sh"
echo ""
echo "To run the production-ready FastAPI server serving both frontend & backend, run:"
echo "   cd backend && .venv/bin/python main.py"
echo "   Then open: http://localhost:8000"
echo "========================================="
