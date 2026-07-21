@echo off
setlocal

cd /d "%~dp0..\..\backend"

if not exist ".venv" (
    echo Error: Python virtual environment not found. Please run scripts\bat\setup.bat first.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo Warning: .env file not found, starting with defaults.
)

.venv\Scripts\python.exe main.py

if errorlevel 1 (
    echo.
    echo EasyPaper exited with an error. See the message above.
    echo.
    pause
)
