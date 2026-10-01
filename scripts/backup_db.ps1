<#
.SYNOPSIS
    scripts/backup_db.ps1 - Automated, online SQLite WAL backup for AAM Merger V3 (SPEC NFR-6).
.DESCRIPTION
    Invokes scripts/backup_db.py to safely checkpoint the WAL journal and stream an
    atomic online backup to data/backup/aam_merger_YYYYMMDD_HHMMSS.db.
.PARAMETER RetentionDays
    Number of days to keep historical backups (default: 30).
.PARAMETER BackupDir
    Destination folder for backups (default: data\backup).
.EXAMPLE
    .\scripts\backup_db.ps1
    .\scripts\backup_db.ps1 -RetentionDays 60
#>
[CmdletBinding()]
param(
    [int]$RetentionDays = 30,
    [string]$BackupDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

# Locate Python
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $PythonExe) {
        Write-Error "python.exe not found. Virtual environment (.venv) or system Python required."
        exit 1
    }
}

$BackupPy = Join-Path $ScriptDir "backup_db.py"

$argsList = @($BackupPy, "--retention-days", "$RetentionDays")
if (-not [string]::IsNullOrWhiteSpace($BackupDir)) {
    $argsList += @("--backup-dir", "$BackupDir")
}

Write-Host "=== AAM Merger V3 - Database Backup ===" -ForegroundColor Cyan
& $PythonExe @argsList
if ($LASTEXITCODE -ne 0) {
    Write-Error "Database backup failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}
