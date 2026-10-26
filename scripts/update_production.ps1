<#
.SYNOPSIS
    scripts/update_production.ps1 - Safe production update and restart runner.
.DESCRIPTION
    Applies application updates safely on Windows Server 2016:
      1. Verifies Administrator privileges.
      2. Performs an automated pre-update backup of the SQLite database.
      3. Stops running Windows Services.
      4. Pulls latest changes from Git (if in a Git repository).
      5. Syncs dependencies via uv or pip.
      6. Applies database schema migrations (scripts/migrate_db.py).
      7. Re-registers Prefect deployment (scripts/deploy_prefect.py).
      8. Starts Windows Services and verifies health.
.EXAMPLE
    powershell -File .\scripts\update_production.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipGitPull
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "             AAM Merger V3 - Production Application Updater           " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Admin Check
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Administrator privileges are required to stop and restart services." -ForegroundColor Red
    exit 1
}

# 2. Pre-update Database Backup
Write-Host "[1/6] Performing pre-update database backup..." -ForegroundColor Cyan
$backupScript = Join-Path $ProjectRoot "scripts\backup_db.ps1"
if (Test-Path $backupScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $backupScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Pre-update backup failed. Proceed with caution." -ForegroundColor Yellow
    }
}

# 3. Stop Services
Write-Host "[2/6] Stopping services..." -ForegroundColor Cyan
$manageScript = Join-Path $ProjectRoot "scripts\manage_services.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $manageScript -Action stop

# 4. Git Pull (if applicable)
Write-Host "[3/6] Fetching latest application code..." -ForegroundColor Cyan
if (-not $SkipGitPull -and (Test-Path ".git")) {
    $hasGit = Get-Command git -ErrorAction SilentlyContinue
    if ($hasGit) {
        Write-Host "  Running git pull..." -ForegroundColor Gray
        & git pull
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: git pull returned non-zero code. Local modifications may exist." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Git command not found on PATH. Skipping pull." -ForegroundColor Gray
    }
} else {
    Write-Host "  Skipping git pull." -ForegroundColor Gray
}

# 5. Sync Dependencies & Database Migration
Write-Host "[4/6] Syncing dependencies & applying schema migrations..." -ForegroundColor Cyan
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}

$hasUv = Get-Command uv -ErrorAction SilentlyContinue
if ($hasUv) {
    & uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Dependency sync failed (uv sync exited with code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
} elseif ($PythonExe -and (Test-Path "requirements.txt")) {
    & $PythonExe -m pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Dependency installation failed (pip exited with code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "ERROR: Neither 'uv' nor 'requirements.txt' found to install dependencies." -ForegroundColor Red
    exit 1
}

$migrateScript = Join-Path $ProjectRoot "scripts\migrate_db.py"
& $PythonExe $migrateScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Database migration failed. Services will NOT be started to protect data." -ForegroundColor Red
    exit 1
}

# 6. Start Services
Write-Host "[5/6] Starting services..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $manageScript -Action start

# 7. Re-register Prefect Deployment & Verify Health
Write-Host "[6/6] Updating Prefect deployment & verifying health..." -ForegroundColor Cyan
$prefectApi = "http://127.0.0.1:4200/api"
$env:PREFECT_API_URL = $prefectApi
$healthUrl = $prefectApi.TrimEnd('/') + "/health"

# Wait for Prefect API to respond
for ($i = 1; $i -le 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { break }
    } catch {
        Start-Sleep -Seconds 1
    }
}

$deployPrefScript = Join-Path $ProjectRoot "scripts\deploy_prefect.py"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$prefOut = & $PythonExe $deployPrefScript 2>&1
$prefCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($prefCode -ne 0) {
    Write-Host "WARNING: Prefect deployment returned code $($prefCode):" -ForegroundColor Yellow
    foreach ($l in @($prefOut)) { Write-Host "  $l" -ForegroundColor Yellow }
} else {
    Write-Host "  Prefect midnight sync updated successfully." -ForegroundColor Green
    $workerSvc = Get-Service -Name "AAMMerger-Worker" -ErrorAction SilentlyContinue
    if ($workerSvc) {
        Restart-Service -Name "AAMMerger-Worker" -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 2
$verifyScript = Join-Path $ProjectRoot "scripts\verify_deployment.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript

Write-Host ""
Write-Host "Update completed successfully." -ForegroundColor Green
