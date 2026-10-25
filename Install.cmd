@echo off
rem Double-click installer: asks for Administrator rights, then runs the one-command setup.
rem Runs scripts\setup_test_rig.ps1 with firewall rule, daily backup and the local Prefect server.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Recurse -Filter *.ps1 | Unblock-File; & '.\scripts\setup_test_rig.ps1' -OpenFirewall -ScheduleBackup -WithPrefectServer"
echo.
echo Setup finished (exit code %errorlevel%). Read the messages above, then press any key.
pause >nul
