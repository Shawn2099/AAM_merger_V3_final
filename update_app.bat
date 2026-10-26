@echo off
setlocal
title AAM Merger V3 - Application Updater
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Administrator privileges are required to update and restart Windows Services.
    echo Please right-click update_app.bat and select "Run as Administrator".
    echo.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_production.ps1"

echo.
pause
