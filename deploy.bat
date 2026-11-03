@echo off
setlocal
title AAM Merger V3 - Deployment Installer
cd /d "%~dp0"

echo ======================================================================
echo           AAM Merger V3 - One-Click Production Deployment
echo ======================================================================
echo.
echo This uses scripts\setup_test_rig.ps1, the handover installer.
echo (scripts\deploy_production.ps1 is retained as legacy and is not used.)
echo.

:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Administrator privileges are required to configure Windows Services.
    echo Please right-click deploy.bat and select "Run as Administrator".
    echo.
    pause
    exit /b 1
)

:: One installer, one code path: same as Install.cmd.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Recurse -Filter *.ps1 | Unblock-File; & '.\scripts\setup_test_rig.ps1' -OpenFirewall -ScheduleBackup -WithPrefectServer"
set "RC=%errorlevel%"

echo.
echo Setup finished (exit code %RC%). Read the messages above.
echo Press any key to close this window...
pause >nul
exit /b %RC%
