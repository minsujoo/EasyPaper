@echo off
setlocal enabledelayedexpansion

REM EasyPaper 데이터 백업 스크립트 (Windows)
REM DB(easypaper.db) + library\ + uploads\를 타임스탬프가 찍힌 zip 파일로
REM backups\ 디렉터리에 저장하고, 오래된 백업은 자동으로 정리합니다.
REM cache\는 재생성 가능한 파생 데이터라 백업 대상에서 제외합니다.

cd /d "%~dp0..\.."

set "BACKUP_DIR=backups"
if not defined EASYPAPER_BACKUP_KEEP set "EASYPAPER_BACKUP_KEEP=10"

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set "DT=%%I"
set "TIMESTAMP=%DT:~0,8%_%DT:~8,6%"
set "ARCHIVE_NAME=easypaper_backup_%TIMESTAMP%.zip"

echo 📦 EasyPaper 데이터 백업 시작...

set "HAS_TARGET=0"
if exist "backend\easypaper.db" set "HAS_TARGET=1"
if exist "backend\library" set "HAS_TARGET=1"
if exist "backend\uploads" set "HAS_TARGET=1"

if "%HAS_TARGET%"=="0" (
    echo 백업할 데이터^(DB/library/uploads^)가 없습니다. 아직 실행되지 않은 설치일 수 있습니다.
    exit /b 0
)

powershell -NoProfile -Command ^
    "$items = @(); " ^
    "if (Test-Path 'backend\easypaper.db') { $items += 'backend\easypaper.db' }; " ^
    "if (Test-Path 'backend\library') { $items += 'backend\library' }; " ^
    "if (Test-Path 'backend\uploads') { $items += 'backend\uploads' }; " ^
    "Compress-Archive -Path $items -DestinationPath '%BACKUP_DIR%\%ARCHIVE_NAME%' -Force"

if errorlevel 1 (
    echo 백업 실패.
    exit /b 1
)

echo 백업 완료: %BACKUP_DIR%\%ARCHIVE_NAME%

REM 오래된 백업 정리 (최신 EASYPAPER_BACKUP_KEEP개만 유지)
powershell -NoProfile -Command ^
    "$keep = %EASYPAPER_BACKUP_KEEP%; " ^
    "$files = Get-ChildItem '%BACKUP_DIR%\easypaper_backup_*.zip' | Sort-Object LastWriteTime -Descending; " ^
    "if ($files.Count -gt $keep) { $files | Select-Object -Skip $keep | Remove-Item -Force; Write-Host ('오래된 백업 ' + ($files.Count - $keep) + '개를 정리했습니다.') }"

echo 현재 보관 중인 백업 목록:
dir /b /o-d "%BACKUP_DIR%\easypaper_backup_*.zip" 2>nul

pause
