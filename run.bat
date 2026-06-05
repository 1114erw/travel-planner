@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ============================================================
echo   Travel Planner - Starting Server
echo ============================================================
echo.
echo Starting server on http://127.0.0.1:5000/
echo Press Ctrl+C to stop
echo.
python app.py
pause
