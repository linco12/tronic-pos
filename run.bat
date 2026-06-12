@echo off
title Tronic POS System
color 0A
echo.
echo  ============================================================
echo   Tronic POS System - Zimbabwe Edition (Multi-Tenant)
echo  ============================================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install from python.org
    pause & exit /b 1
)

echo  Installing / checking dependencies...
pip install -r requirements.txt -q

echo  Initialising database and admin account...
python startup.py

echo  Starting server at http://localhost:5000
echo  Admin login: lincolnmotiwac@gmail.com
echo  Press Ctrl+C to stop.
echo.
timeout /t 2 /nobreak >nul
start http://localhost:5000
python app.py

pause
