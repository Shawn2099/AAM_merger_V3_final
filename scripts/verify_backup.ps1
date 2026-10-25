<#
.SYNOPSIS
    scripts/verify_backup.ps1 - Check that a database backup matches the live database.
.DESCRIPTION
    Opens both SQLite files read-only (via scripts/_verify_backup.py), compares the
    row counts of documents, line_items, po_sets and audit_log, and checks the SHA-256
    of up to 3 stored PDFs named in the backup's documents table. Prints a PASS/FAIL
    table and exits 1 on any FAIL (2 if the check could not run).
.PARAMETER Source
    Live database file, or config.yaml (its paths.database_path is used).
.PARAMETER Backup
    Backup database file, e.g. data\development\snapshots\db\aam_merger_<stamp>.db
.PARAMETER AllowSourceNewer
    Accept a backup that has FEWER rows than the live database (the app kept
    working after the backup was taken). Without it counts must be equal.
.EXAMPLE
    .\scripts\verify_backup.ps1 -Source .\config.yaml -Backup .\data\development\snapshots\db\aam_merger_X.db -AllowSourceNewer
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Backup,
    [string]$ProjectDir = "",
    [switch]$AllowSourceNewer
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ProjectDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
}
$venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $py = $venvPython
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Host "ERROR: python not found (no .venv and none on PATH)." -ForegroundColor Red
        exit 2
    }
    $py = $cmd.Source
}

$helper = Join-Path $ScriptDir "_verify_backup.py"
$pyArgs = @($helper, "--source", $Source, "--backup", $Backup, "--root", $ProjectDir)
if ($AllowSourceNewer) { $pyArgs += "--allow-source-newer" }
& $py @pyArgs
exit $LASTEXITCODE
