@echo off
setlocal

cd /d "%~dp0backend"

if not exist ".venv" (
    echo Error: Python virtual environment not found. Please run setup.bat first.
    exit /b 1
)

if not exist ".env" (
    echo Warning: .env file not found, starting with defaults.
)

.venv\Scripts\python.exe main.py
