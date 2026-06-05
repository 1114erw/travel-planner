@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

echo ========================================
echo   Travel Planner Server Starting...
echo ========================================
echo.

REM Check if port 5000 is already in use...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":5000 "') do (
    echo Found process PID %%P is using port 5000
    echo Killing process PID %%P...
    taskkill /F /PID %%P >nul 2>&1
    timeout /t 2 /nobreak >nul
)

echo.
echo Starting server on http://127.0.0.1:5000 ...
echo.
echo To stop the server, close this window or press Ctrl+C.
echo ========================================
echo.

python app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Server failed to start. Error code: %errorlevel%
    echo.
    pause
)

endlocal
