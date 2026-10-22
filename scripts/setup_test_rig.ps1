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
.PARAMETER PrefectApiUrl
    Prefect API URL for the web app, worker and CLI. Defaults to
    http://127.0.0.1:4200/api when -WithPrefectServer is used. Point it at an
    external Prefect server or Prefect Cloud instead if you have one.
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
    [string]$PrefectApiUrl = "",
    [switch]$SkipServices
)

$ErrorActionPreference = "Stop"

# Prefect's console renderer draws box characters. A Windows console defaults
# to cp1252, so the CLI did its work and then crashed while printing the
# result — "Work pool create" exited 1 even though the pool existed. UTF-8 mode
# makes the exit code tell the truth.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       AAM Merger V3 - Production / Test Rig Setup          " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Administrator check.
# Files, config and migrations need no elevation; services, firewall and the
# backup task do. Requiring admin for a files-only setup blocked a non-admin
# operator from preparing a machine at all.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$needsAdmin = $OpenFirewall -or $ScheduleBackup -or (-not $SkipServices)
if ($needsAdmin -and -not $isAdmin) {
    Write-Error "Administrator privileges are required to configure services, firewall, and tasks. Re-run PowerShell as Administrator, or pass -SkipServices for a files-only setup."
    exit 1
}
if (-not $isAdmin) {
    Write-Warning "Not running as Administrator: installing files, config and database only. Services, firewall and the backup task are skipped."
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

Write-Host "[1/8] Verifying Project Root and Python Environment..." -ForegroundColor Cyan
Write-Host "  Project Root: $ProjectRoot"

# Find uv or python
$hasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not $hasUv -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Neither 'uv' nor 'python' is on PATH. Install Python 3.11+ (and optionally uv) from python.org, then re-run."
    exit 1
}

if ($hasUv) {
    # Idempotent: creates .venv if missing and repairs a stale one, so a
    # re-run after pulling an update does not silently keep old dependencies.
    # Deployment machines do not need the test tooling; a development machine
    # should run `uv sync --all-groups` afterwards.
    Write-Host "  Syncing dependencies with uv..."
    uv sync --no-dev
    if ($LASTEXITCODE -ne 0) {
        Write-Error "uv sync failed."
        exit $LASTEXITCODE
    }
} else {
    if (-not (Test-Path $venvPython)) {
        Write-Host "  .venv not found. Creating virtual environment..." -ForegroundColor Yellow
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Could not create .venv. Install Python 3.11+ and re-run, or install uv."
            exit $LASTEXITCODE
        }
        & $venvPython -m pip install --upgrade pip
    }
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dependency installation failed."
        exit $LASTEXITCODE
    }
}
if (-not (Test-Path $venvPython)) {
    Write-Error "Python environment missing at $venvPython."
    exit 1
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
    $action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`"" -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At 1am
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "  Registered scheduled task '$taskName' at 01:00 AM daily." -ForegroundColor Green
} else {
    Write-Host "[5/8] Skipping Scheduled Backup Task registration (-ScheduleBackup not set)." -ForegroundColor Gray
}

# 6. Install NSSM Services (must exist before the Prefect pool/deployment is
#    registered: the worker and the CLI have to point at the same API).
if (-not $SkipServices) {
    Write-Host "[6/8] Installing Windows Services via NSSM..." -ForegroundColor Cyan
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
    if (-not [string]::IsNullOrWhiteSpace($PrefectApiUrl)) {
        $installParams["PrefectApiUrl"] = $PrefectApiUrl
    }
    & $installScript @installParams
} else {
    Write-Host "[6/8] Skipping NSSM service installation (-SkipServices specified)." -ForegroundColor Gray
}

# 7. Prefect: create the work pool and register the midnight deployment.
#    The worker command names a pool, but nothing created it: on a fresh host
#    `deploy_prefect.py` failed with "Work pool not found" and the nightly sync
#    was never scheduled. An API must be reachable for any of this to mean
#    anything, so say so plainly instead of registering into a throwaway server.
Write-Host "[7/8] Configuring Prefect (work pool + midnight deployment)..." -ForegroundColor Cyan
$prefectApi = $PrefectApiUrl
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $WithPrefectServer) {
    $prefectApi = "http://127.0.0.1:4200/api"
}
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $env:PREFECT_API_URL) {
    $prefectApi = $env:PREFECT_API_URL
}

$poolName = (& $venvPython -c "import sys; sys.path.insert(0,'src'); from app.core.config import load_config; print(load_config().prefect.work_pool_name)").Trim()
$apiUp = $false
if (-not [string]::IsNullOrWhiteSpace($prefectApi)) {
    $env:PREFECT_API_URL = $prefectApi
    Write-Host "  Prefect API: $prefectApi"
    $healthUrl = $prefectApi.TrimEnd('/') + "/health"
    for ($i = 1; $i -le 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $apiUp = $true; break }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $apiUp) {
        Write-Warning "Prefect API at $prefectApi did not answer within 30s."
    }
}

if ($apiUp) {
    & $venvPython -m prefect work-pool create $poolName --type process --overwrite
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not create work pool '$poolName'. Check the Prefect server log."
    } else {
        Write-Host "  Work pool '$poolName' ready." -ForegroundColor Green
    }

    $deployScript = Join-Path $ProjectRoot "scripts\deploy_prefect.py"
    & $venvPython $deployScript
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Prefect deployment registration failed. The scheduled sync will not run until this succeeds."
    }

    # The worker may have started before the pool existed; restart it so it
    # picks up the pool and the deployment.
    if (-not $SkipServices) {
        $worker = Get-Service -Name "AAMMerger-Worker" -ErrorAction SilentlyContinue
        if ($worker) {
            Restart-Service -Name "AAMMerger-Worker" -Force -ErrorAction SilentlyContinue
            Write-Host "  Worker restarted against pool '$poolName'." -ForegroundColor Green
        }
    }
} elseif ([string]::IsNullOrWhiteSpace($prefectApi)) {
    Write-Warning "No Prefect API configured, so the midnight sync was not scheduled. Manual 'Scan Input Folder' still works. Re-run with -WithPrefectServer (single host) or set PREFECT_API_URL."
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
# A handover URL of http://0.0.0.0:8000 is not something a user can open.
# Show the LAN address this machine is actually reachable on.
$displayHost = $HostAddress
if ($HostAddress -eq "0.0.0.0") {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' } |
        Select-Object -First 1).IPAddress
    if ($lanIp) { $displayHost = $lanIp }
}
Write-Host "Web Dashboard URL : http://${displayHost}:${Port}/dashboard"
Write-Host "Service Status    : .\scripts\manage_services.ps1 -Action status"
Write-Host "Backup DB         : .\scripts\backup_db.ps1"
Write-Host "Logs Directory    : $ProjectRoot\data\logs\"
