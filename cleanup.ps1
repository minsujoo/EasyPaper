# EasyPaper Clean up & Uninstall Script (Windows PowerShell)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "========================================="
Write-Host "EasyPaper Uninstall & Clean up Script"
Write-Host "========================================="

# 참고: systemd 서비스 등록은 Linux 전용 기능이라 Windows에서는 해당 사항이 없습니다.

# 1. Clean up Backend environment
Write-Host "1. Cleaning up Backend..."
if (Test-Path "backend\.venv") {
    Write-Host "   Removing Python virtual environment (backend\.venv)..."
    Remove-Item -Recurse -Force "backend\.venv"
}

if (Test-Path "backend\.env") {
    Write-Host "   Removing configuration file (backend\.env)..."
    Remove-Item -Force "backend\.env"
}

# 2. Clean up Frontend environment
Write-Host "2. Cleaning up Frontend..."
if (Test-Path "frontend\node_modules") {
    Write-Host "   Removing node_modules..."
    Remove-Item -Recurse -Force "frontend\node_modules"
}

if (Test-Path "frontend\dist") {
    Write-Host "   Removing production build assets (frontend\dist)..."
    Remove-Item -Recurse -Force "frontend\dist"
}

# 3. Prompt to clean up database & library data
Write-Host "========================================="
$reply = Read-Host "Do you want to delete all database and document data? (y/N)"
if ($reply -match '^[Yy]$') {
    Write-Host "3. Deleting all database, uploads, and library files..."

    if (Test-Path "backend\easypaper.db") {
        Write-Host "   Deleting SQLite database (backend\easypaper.db)..."
        Remove-Item -Force "backend\easypaper.db"
    }

    $dirsToRemove = @("uploads", "library", "cache", "backend\uploads", "backend\cache")
    foreach ($dir in $dirsToRemove) {
        if (Test-Path $dir) {
            Write-Host "   Removing directory: $dir..."
            Remove-Item -Recurse -Force $dir
        }
    }
} else {
    Write-Host "3. Retaining database and document files (uploads/, library/, easypaper.db)."
}

Write-Host "========================================="
Write-Host "EasyPaper Clean up Complete!"
Write-Host "========================================="
