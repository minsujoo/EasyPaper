@echo off
setlocal

echo EasyPaper development servers starting...

cd /d "%~dp0backend"
start "EasyPaper Backend" cmd /k .venv\Scripts\python.exe main.py

cd /d "%~dp0frontend"
start "EasyPaper Frontend" cmd /k npm run dev

echo.
echo EasyPaper running!
echo    Frontend:    http://localhost:5173
echo    Backend API: http://localhost:8000
echo    API Docs:    http://localhost:8000/docs
echo.
echo Backend and frontend are each running in their own window.
echo Close those windows (or Ctrl+C inside them) to stop the servers.
