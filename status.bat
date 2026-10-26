@echo off
setlocal
title AAM Merger V3 - System Status
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\manage_services.ps1" -Action status
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\verify_deployment.ps1"

echo.
pause
