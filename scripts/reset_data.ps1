<#
.SYNOPSIS
    scripts/reset_data.ps1 - Return AAM Merger V3 to a clean slate for a new test run.

.DESCRIPTION
    Backs up the database, deletes every ingested document and generated
    packet, recreates the configured folders, and re-applies the schema.

    What is removed:
      - the SQLite database (plus -wal / -shm)
      - every file in the input, output, quarantine, stored and combined folders

    What is kept:
      - config.yaml and .env (your paths, model and API key)
      - data\backup\* (the pre-reset backup lands here)
      - logs

    This is destructive. It asks for confirmation unless -Force is passed.

.PARAMETER Force
    Skip the confirmation prompt. Still refuses to delete if the web server is
    running (stop it first, or stop the AAMMerger services).

.PARAMETER ProjectDir
    Project root. Defaults to the parent of this script.

.EXAMPLE
    .\scripts\reset_data.ps1
    .\scripts\reset_data.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [string]$ProjectDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ProjectDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
}
$ProjectRoot = (Resolve-Path $ProjectDir).Path
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Python environment not found at $venvPython. Run scripts\setup_test_rig.ps1 first."
    exit 1
}

if (-not (Test-Path (Join-Path $ProjectRoot "config.yaml"))) {
    Write-Error "config.yaml not found in $ProjectRoot. Copy config.example.yaml and set the paths first."
    exit 1
}

# Resolve the same paths the application uses (relative paths are relative to
# the project root, exactly as they are for the web service).
$reader = @"
import json, sys
sys.path.insert(0, 'src')
from app.core.config import load_config
c = load_config()
keys = ['database_path', 'input_folder', 'output_folder', 'quarantine_folder',
        'stored_documents_folder', 'combined_folder']
print(json.dumps({k: str(getattr(c.paths, k)) for k in keys} | {'port': c.server.port}))
"@
$pathsJson = & $venvPython -c $reader 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pathsJson)) {
    Write-Error "Could not read config.yaml. Fix it and re-run."
    exit 1
}
$paths = $pathsJson | ConvertFrom-Json

# 1. Refuse while the server is listening: deleting a live SQLite database
#    produces a half-removed state and misleading errors.
$listeners = Get-NetTCPConnection -LocalPort $paths.port -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    Write-Error "Port $($paths.port) is still in use. Stop the AAMMerger services (.\scripts\manage_services.ps1 -Action stop) or the Uvicorn process, then re-run."
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$services = Get-Service -Name "AAMMerger-*" -ErrorAction SilentlyContinue
if ($services) {
    if (-not $isAdmin) {
        Write-Error "AAMMerger services are installed. Stop them as Administrator first, then re-run."
        exit 1
    }
    $services | Where-Object { $_.Status -eq 'Running' } | Stop-Service -Force
}

# 2. Show exactly what will be deleted, then confirm.
$folders = @(
    $paths.input_folder,
    $paths.output_folder,
    $paths.quarantine_folder,
    $paths.stored_documents_folder,
    $paths.combined_folder
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

$totalFiles = 0
foreach ($folder in $folders) {
    $count = (Get-ChildItem -Force $folder -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne '.gitkeep' }).Count
    $totalFiles += $count
}

Write-Host "=== AAM Merger V3 - Reset test data ===" -ForegroundColor Cyan
Write-Host "Project      : $ProjectRoot"
Write-Host "Database     : $($paths.database_path)"
Write-Host "Folders      : $($folders.Count) ($totalFiles files)"
foreach ($folder in $folders) { Write-Host "  - $folder" }
Write-Host "Kept         : config.yaml, .env, data\backup, logs" -ForegroundColor Green

if (-not $Force) {
    $answer = Read-Host "Type RESET to delete this data (anything else cancels)"
    if ($answer -ne "RESET") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        exit 0
    }
}

# 3. Back up the database before touching it (only if it exists).
$dbPath = $paths.database_path
if (Test-Path $dbPath) {
    Write-Host "Backing up database..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "backup_db.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Database backup failed. Nothing was deleted."
        exit $LASTEXITCODE
    }
}

# 4. Delete the database files and the folder contents (keep .gitkeep).
foreach ($suffix in @("", "-wal", "-shm")) {
    $candidate = "$dbPath$suffix"
    if (Test-Path $candidate) {
        Remove-Item -Force $candidate
        Write-Host "  removed $candidate"
    }
}

foreach ($folder in $folders) {
    $resolved = (Resolve-Path $folder).Path.TrimEnd('\')
    if ($resolved -eq "" -or $resolved -eq $ProjectRoot.TrimEnd('\')) {
        Write-Error "Refusing to clear '$folder': it resolves to the project root."
        exit 1
    }
    Get-ChildItem -Force $folder -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '.gitkeep' } |
        Remove-Item -Recurse -Force
    Write-Host "  cleared $folder"
}

# 5. Recreate the schema on a fresh database.
$migrate = "import sys; sys.path.insert(0, 'src'); from alembic.config import main; main(argv=['upgrade', 'head'])"
& $venvPython -c $migrate
if ($LASTEXITCODE -ne 0) {
    Write-Error "Database migration failed. The database is empty; fix the error and re-run."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Reset complete. Fresh database at $dbPath." -ForegroundColor Green
Write-Host "Start the services, then open the dashboard." -ForegroundColor Green
