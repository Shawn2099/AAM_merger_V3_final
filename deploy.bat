@echo off
setlocal
title AAM Merger V3 - Deployment Installer
cd /d "%~dp0"

echo ======================================================================
echo           AAM Merger V3 - One-Click Production Deployment
echo ======================================================================
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

:: Run PowerShell deployment script with ExecutionPolicy Bypass
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy_production.ps1"

echo.
echo Press any key to close this window...
pause >nul
