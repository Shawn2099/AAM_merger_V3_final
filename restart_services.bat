@echo off
setlocal
title AAM Merger V3 - Restart Services
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Administrator privileges are required to restart Windows Services.
    echo Please right-click restart_services.bat and select "Run as Administrator".
    echo.
    pause
    exit /b 1
)

echo Restarting AAM Merger V3 Windows Services...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\manage_services.ps1" -Action restart

echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\manage_services.ps1" -Action status
echo.
pause
