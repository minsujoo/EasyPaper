@echo off
setlocal

REM 저장소 루트 기준으로 동작하도록 이동 (이 스크립트는 scripts\bat\ 하위에 있음)
cd /d "%~dp0..\.."

echo =========================================
echo EasyPaper Auto Setup ^& Installation Script
echo =========================================

where python 2>nul | findstr /i "WindowsApps" >nul
if not errorlevel 1 (
    echo Error: 'python' currently points to the Windows Store app-execution-alias
    echo stub, not a real Python installation ^(this stub only prints "Python" and
    echo exits without doing anything^).
    echo.
    echo If you installed Python via Miniforge/Anaconda/Miniconda: run this script
    echo from an "Anaconda Prompt" / "Miniforge Prompt" ^(where the base environment
    echo is already activated^) instead of double-clicking it from Explorer.
    echo.
    echo Otherwise, disable the fake alias at:
    echo   Settings ^> Apps ^> Advanced app settings ^> App execution aliases
    echo   ^(turn off "python.exe" / "python3.exe"^)
    goto :error
)

python --version >nul 2>nul
if errorlevel 1 (
    echo Error: 'python' command not found.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    echo and make sure to check "Add python.exe to PATH" during installation.
    echo ^(If you use Miniforge/Anaconda, run this from an Anaconda/Miniforge Prompt.^)
    goto :error
)

where npm >nul 2>nul
if errorlevel 1 (
    echo Error: 'npm' command not found.
    echo Please install Node.js 16+ from https://nodejs.org/ ^(npm is included^).
    goto :error
)

echo 1. Setting up Backend...
cd backend

if not exist ".venv" (
    echo    Creating Python virtual environment ^(.venv^)...
    python -m venv .venv
    if errorlevel 1 goto :error
)

echo    Installing backend dependencies...
.venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 goto :error

if not exist ".env" (
    echo    Creating configuration file ^(.env from template^)...
    copy /y ".env.example" ".env" >nul
)

cd ..

echo 2. Setting up Frontend...
cd frontend

echo    Installing frontend dependencies ^(npm install^)...
call npm install
if errorlevel 1 goto :error

echo    Building frontend production assets...
call npm run build
if errorlevel 1 goto :error

cd ..

echo =========================================
echo EasyPaper Setup Complete!
echo =========================================
echo To start the development servers concurrently, run:
echo    scripts\bat\start-dev.bat
echo.
echo To run the production-ready server serving both frontend ^& backend, run:
echo    scripts\bat\start.bat
echo    Then open: http://localhost:8000
echo =========================================
echo.
pause
goto :eof

:error
echo.
echo Setup failed. Please check the error message above.
echo.
pause
exit /b 1
