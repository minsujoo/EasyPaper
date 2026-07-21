# EasyPaper Development Server Start Script (Windows PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "EasyPaper 개발 서버 시작..."

# 백엔드 시작
Write-Host "FastAPI 백엔드 시작 중 (포트 8000)..."
$backend = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "main.py" `
    -WorkingDirectory (Join-Path $PSScriptRoot "backend") -PassThru -NoNewWindow
Write-Host "   백엔드 PID: $($backend.Id)"

Start-Sleep -Seconds 2

# 프론트엔드 시작
Write-Host "Vite 프론트엔드 시작 중 (포트 5173)..."
$frontend = Start-Process -FilePath "npm" -ArgumentList "run", "dev" `
    -WorkingDirectory (Join-Path $PSScriptRoot "frontend") -PassThru -NoNewWindow

Write-Host ""
Write-Host "EasyPaper 실행 중!"
Write-Host "   프론트엔드: http://localhost:5173"
Write-Host "   백엔드 API: http://localhost:8000"
Write-Host "   API 문서:   http://localhost:8000/docs"
Write-Host ""
Write-Host "종료하려면 Ctrl+C를 누르세요."

try {
    Wait-Process -Id $backend.Id, $frontend.Id
} finally {
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue
}
