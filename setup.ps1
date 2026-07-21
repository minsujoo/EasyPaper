# EasyPaper Setup & Installation Script (Windows PowerShell)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "========================================="
Write-Host "EasyPaper Auto Setup & Installation Script"
Write-Host "========================================="

# 1. Setup Backend
Write-Host "1. Setting up Backend..."
Set-Location backend

if (-not (Test-Path ".venv")) {
    Write-Host "   Creating Python virtual environment (.venv)..."
    python -m venv .venv
}

Write-Host "   Installing backend dependencies..."
& ".venv\Scripts\pip.exe" install -r requirements.txt

if (-not (Test-Path ".env")) {
    Write-Host "   Creating configuration file (.env from template)..."
    Copy-Item ".env.example" ".env"
}

Set-Location ..

# 2. Setup Frontend
Write-Host "2. Setting up Frontend..."
Set-Location frontend

Write-Host "   Installing frontend dependencies (npm install)..."
npm install

Write-Host "   Building frontend production assets..."
npm run build

Set-Location ..

Write-Host "========================================="
Write-Host "EasyPaper Setup Complete!"
Write-Host "========================================="
Write-Host "To start the development servers concurrently, run:"
Write-Host "   .\start-dev.ps1"
Write-Host ""
Write-Host "To run the production-ready server serving both frontend & backend, run:"
Write-Host "   .\start.ps1"
Write-Host "   Then open: http://localhost:8000"
Write-Host "========================================="
