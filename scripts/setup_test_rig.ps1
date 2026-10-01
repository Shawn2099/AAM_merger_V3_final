<#
.SYNOPSIS
    scripts/setup_test_rig.ps1 - Complete one-click deployment & test rig setup for AAM Merger V3.
.DESCRIPTION
    Automates the entire setup of AAM Merger V3 on a Windows Server 2016 or Windows test rig:
      1. Validates Administrator privileges.
      2. Ensures Python environment (.venv) and dependencies are installed.
      3. Verifies or generates config.yaml from config.example.yaml.
      4. Checks for .env / OPENROUTER_API_KEY.
      5. Creates required data, log, and backup directories.
      6. Applies database schema migrations (alembic upgrade head).
      7. (Optional) Configures Windows Firewall inbound rule for web access.
      8. (Optional) Configures Windows Scheduled Task for daily SQLite WAL backups.
      9. Downloads NSSM (if missing) and installs AAM Merger Windows Services.
     10. Starts services and validates /health endpoint.

.PARAMETER Port
    HTTP Port for Uvicorn web service (default: 8000).
.PARAMETER HostAddress
    Bind address for Uvicorn (default: 0.0.0.0).
.PARAMETER OpenFirewall
    Switch to automatically add a Windows Firewall inbound rule for the web port.
.PARAMETER ScheduleBackup
    Switch to register a daily Windows Scheduled Task for SQLite backups (01:00 AM).
.PARAMETER WithPrefectServer
    Switch to install a local self-hosted Prefect server as a Windows Service.
.PARAMETER SkipServices
    Switch to configure files, database, and schedule without installing NSSM services.

.EXAMPLE
    .\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
#>
[CmdletBinding()]
param(
    [string]$Port = "8000",
    [string]$HostAddress = "0.0.0.0",
    [switch]$OpenFirewall,
    [switch]$ScheduleBackup,
    [switch]$WithPrefectServer,
    [switch]$SkipServices
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       AAM Merger V3 - Production / Test Rig Setup          " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Administrator check
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Administrator privileges are required to configure services, firewall, and tasks. Please launch PowerShell as Administrator."
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

Write-Host "[1/8] Verifying Project Root and Python Environment..." -ForegroundColor Cyan
Write-Host "  Project Root: $ProjectRoot"

# Find uv or python
$hasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "  .venv not found. Creating virtual environment..." -ForegroundColor Yellow
    if ($hasUv) {
        uv venv .venv
        uv sync --no-dev
    } else {
        python -m venv .venv
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -e .
    }
}
Write-Host "  Python: $venvPython" -ForegroundColor Green

# 2. Config & Folders
Write-Host "[2/8] Checking Configuration & Data Directories..." -ForegroundColor Cyan
$configPath = Join-Path $ProjectRoot "config.yaml"
if (-not (Test-Path $configPath)) {
    Write-Host "  config.yaml missing. Copying config.example.yaml..." -ForegroundColor Yellow
    Copy-Item (Join-Path $ProjectRoot "config.example.yaml") $configPath
    Write-Host "  Created config.yaml (please review paths for production)." -ForegroundColor Yellow
} else {
    Write-Host "  config.yaml present." -ForegroundColor Green
}

$envPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envPath)) {
    if (-not $env:OPENROUTER_API_KEY) {
        Write-Warning "  .env file with OPENROUTER_API_KEY is missing! VLM extraction requires an API key."
    }
} else {
    Write-Host "  .env file present." -ForegroundColor Green
}

$dataDirs = @(
    "data\input",
    "data\output",
    "data\quarantine",
    "data\stored",
    "data\combined",
    "data\logs",
    "data\backup",
    "data\samples"
)
foreach ($dir in $dataDirs) {
    $fullPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
}
Write-Host "  Data directories verified." -ForegroundColor Green

# 3. Database Migration
Write-Host "[3/8] Applying SQLite Database Migrations..." -ForegroundColor Cyan
$alembicScript = "import sys; from pathlib import Path; sys.path.insert(0, 'src'); from alembic.config import main; main(argv=['upgrade', 'head'])"
& $venvPython -c $alembicScript
if ($LASTEXITCODE -ne 0) {
    Write-Error "Database migration failed."
    exit $LASTEXITCODE
}
Write-Host "  Database schema up to date." -ForegroundColor Green

# 4. Optional Firewall Rule
if ($OpenFirewall) {
    Write-Host "[4/8] Configuring Windows Firewall..." -ForegroundColor Cyan
    $ruleName = "AAM Merger V3 Web ($Port)"
    $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existingRule) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow | Out-Null
        Write-Host "  Firewall rule created for TCP port $Port." -ForegroundColor Green
    } else {
        Write-Host "  Firewall rule '$ruleName' already exists." -ForegroundColor Green
    }
} else {
    Write-Host "[4/8] Skipping Windows Firewall configuration (-OpenFirewall not set)." -ForegroundColor Gray
}

# 5. Optional Scheduled Task for Backups
if ($ScheduleBackup) {
    Write-Host "[5/8] Configuring Scheduled Task for Daily Database Backups..." -ForegroundColor Cyan
    $taskName = "AAMMerger-DailyBackup"
    $backupScript = Join-Path $ProjectRoot "scripts\backup_db.ps1"
    $action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`""
    $trigger = New-ScheduledTaskTrigger -Daily -At 1am
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "  Registered scheduled task '$taskName' at 01:00 AM daily." -ForegroundColor Green
} else {
    Write-Host "[5/8] Skipping Scheduled Backup Task registration (-ScheduleBackup not set)." -ForegroundColor Gray
}

# 6. Prefect Deployment Registration
Write-Host "[6/8] Registering Prefect Orchestration Deployment..." -ForegroundColor Cyan
$deployScript = Join-Path $ProjectRoot "scripts\deploy_prefect.py"
& $venvPython $deployScript
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Prefect deployment registration returned non-zero code. Worker may register it upon start."
}

# 7. Install NSSM Services
if (-not $SkipServices) {
    Write-Host "[7/8] Installing Windows Services via NSSM..." -ForegroundColor Cyan
    $installScript = Join-Path $ProjectRoot "scripts\install_nssm_services.ps1"
    $installParams = @{
        ProjectDir        = $ProjectRoot
        PythonExe         = $venvPython
        Port              = $Port
        HostAddress       = $HostAddress
        StartServices     = $true
    }
    if ($WithPrefectServer) {
        $installParams["WithPrefectServer"] = $true
    }
    & $installScript @installParams
} else {
    Write-Host "[7/8] Skipping NSSM service installation (-SkipServices specified)." -ForegroundColor Gray
}

# 8. Smoke Check & Verification
Write-Host "[8/8] Performing Smoke Test & Health Check..." -ForegroundColor Cyan
if (-not $SkipServices) {
    $healthy = $false
    $testUrl = "http://127.0.0.1:${Port}/health"
    for ($i = 1; $i -le 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri $testUrl -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) {
                Write-Host "  Health Check OK (HTTP 200) from $testUrl" -ForegroundColor Green
                $healthy = $true
                break
            }
        } catch {
            # retry
        }
    }
    if (-not $healthy) {
        Write-Warning "Health check did not respond yet. Check logs in data\logs\nssm-web-stdout.log"
    }
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "       AAM Merger V3 Setup Completed Successfully!          " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Web Dashboard URL : http://${HostAddress}:${Port}/dashboard"
Write-Host "Service Status    : .\scripts\manage_services.ps1 -Action status"
Write-Host "Backup DB         : .\scripts\backup_db.ps1"
Write-Host "Logs Directory    : $ProjectRoot\data\logs\"
