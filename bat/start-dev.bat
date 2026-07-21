@echo off
setlocal

cd /d "%~dp0.."

if not exist "backend\.venv" (
    echo Error: Python virtual environment not found. Please run bat\setup.bat first.
    echo.
    pause
    exit /b 1
)

echo EasyPaper development servers starting...

cd /d "%~dp0..\backend"
start "EasyPaper Backend" cmd /k .venv\Scripts\python.exe main.py

cd /d "%~dp0..\frontend"
start "EasyPaper Frontend" cmd /k npm run dev

echo.
echo EasyPaper running!
echo    Frontend:    http://localhost:5173
echo    Backend API: http://localhost:8000
echo    API Docs:    http://localhost:8000/docs
echo.
echo Backend and frontend are each running in their own window.
echo Close those windows (or Ctrl+C inside them) to stop the servers.
echo.
pause
