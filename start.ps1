# EasyPaper Production Server Start Script (Windows PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "EasyPaper 서버 시작 중..."

# Ensure we run from backend folder with .venv python
Set-Location (Join-Path $PSScriptRoot "backend")

if (-not (Test-Path ".venv")) {
    Write-Host "Error: Python 가상환경이 존재하지 않습니다. 먼저 '.\setup.ps1'을 실행해주세요."
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "Warning: .env 파일이 존재하지 않아 기본값으로 시작합니다."
}

# Run backend which also serves the compiled frontend dist
& ".venv\Scripts\python.exe" main.py
