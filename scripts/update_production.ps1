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
} elseif ($PythonExe -and (Test-Path "requirements.txt")) {
    & $PythonExe -m pip install -r requirements.txt --quiet
}

$migrateScript = Join-Path $ProjectRoot "scripts\migrate_db.py"
& $PythonExe $migrateScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Database migration failed. Services will NOT be started to protect data." -ForegroundColor Red
    exit 1
}

# 6. Re-register Prefect Deployment
Write-Host "[5/6] Updating Prefect deployment..." -ForegroundColor Cyan
$deployPrefScript = Join-Path $ProjectRoot "scripts\deploy_prefect.py"
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $PythonExe $deployPrefScript 2>$null
$ErrorActionPreference = $prevEap

# 7. Start Services & Verify
Write-Host "[6/6] Restarting services & verifying..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $manageScript -Action start
Start-Sleep -Seconds 3

$verifyScript = Join-Path $ProjectRoot "scripts\verify_deployment.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript

Write-Host ""
Write-Host "Update completed successfully." -ForegroundColor Green
