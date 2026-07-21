@echo off
setlocal

cd /d "%~dp0"

echo =========================================
echo EasyPaper Uninstall ^& Clean up Script
echo =========================================

echo 1. Cleaning up Backend...
if exist "backend\.venv" (
    echo    Removing Python virtual environment ^(backend\.venv^)...
    rmdir /s /q "backend\.venv"
)
if exist "backend\.env" (
    echo    Removing configuration file ^(backend\.env^)...
    del /f /q "backend\.env"
)

echo 2. Cleaning up Frontend...
if exist "frontend\node_modules" (
    echo    Removing node_modules...
    rmdir /s /q "frontend\node_modules"
)
if exist "frontend\dist" (
    echo    Removing production build assets ^(frontend\dist^)...
    rmdir /s /q "frontend\dist"
)

echo =========================================
set /p REPLY="Do you want to delete all database and document data? (y/N) "
if /i "%REPLY%"=="y" (
    echo 3. Deleting all database, uploads, and library files...
    if exist "backend\easypaper.db" (
        echo    Deleting SQLite database ^(backend\easypaper.db^)...
        del /f /q "backend\easypaper.db"
    )
    for %%D in (uploads library cache backend\uploads backend\cache) do (
        if exist "%%D" (
            echo    Removing directory: %%D...
            rmdir /s /q "%%D"
        )
    )
) else (
    echo 3. Retaining database and document files ^(uploads/, library/, easypaper.db^).
)

echo =========================================
echo EasyPaper Clean up Complete!
echo =========================================
