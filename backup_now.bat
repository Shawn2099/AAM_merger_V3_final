@echo off
setlocal
title AAM Merger V3 - Instant Database Backup
cd /d "%~dp0"

echo Initiating immediate online database backup...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backup_db.ps1"

echo.
pause
