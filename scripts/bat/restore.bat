@echo off
setlocal

REM EasyPaper 데이터 복원 스크립트 (Windows)
REM backup.bat으로 만든 백업 zip 파일에서 DB/library/uploads를 복원합니다.

cd /d "%~dp0..\.."

if "%~1"=="" (
    echo 사용법: %~nx0 ^<백업파일.zip^>
    echo.
    echo 사용 가능한 백업 목록:
    dir /b /o-d "backups\easypaper_backup_*.zip" 2>nul
    exit /b 1
)

set "BACKUP_FILE=%~1"
if not exist "%BACKUP_FILE%" (
    echo 백업 파일을 찾을 수 없습니다: %BACKUP_FILE%
    exit /b 1
)

echo 이 작업은 현재 backend\easypaper.db, backend\library\, backend\uploads\를
echo 백업 파일 시점의 데이터로 덮어씁니다.
set /p REPLY="계속하시겠습니까? (y/N) "
if /i not "%REPLY%"=="y" (
    echo 취소되었습니다.
    exit /b 0
)

echo 복원 중: %BACKUP_FILE% ...
powershell -NoProfile -Command "Expand-Archive -Path '%BACKUP_FILE%' -DestinationPath '.' -Force"

if errorlevel 1 (
    echo 복원 실패.
    exit /b 1
)

echo 복원 완료. 서비스를 재시작해주세요 ^(scripts\bat\start.bat^).
pause
