@echo off
setlocal

cd /d "%~dp0"

echo =========================================
echo EasyPaper Auto Setup ^& Installation Script
echo =========================================

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
echo    start-dev.bat
echo.
echo To run the production-ready server serving both frontend ^& backend, run:
echo    start.bat
echo    Then open: http://localhost:8000
echo =========================================
goto :eof

:error
echo.
echo Setup failed. Please check the error message above.
exit /b 1
