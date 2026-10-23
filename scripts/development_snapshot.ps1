<#
.SYNOPSIS
    Copy the pilot's live logs and document folders into data/development/snapshots.

.DESCRIPTION
    Copy-only. Nothing is pruned and nothing is moved out of the live folders.
    robocopy /E /XO copies new/changed files and never deletes destination
    content. This is an ops task, not application code.

    The database has its own online backup task
    (scripts/backup_db.ps1 -RetentionDays 0), which writes to
    data/development/snapshots/db.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DataRoot = Join-Path $ProjectRoot "data"
$DestRoot = Join-Path $DataRoot "development\snapshots"

$Pairs = @(
    @("logs", "logs"),
    @("raw_extractions", "raw_extractions"),
    @("stored", "files\stored"),
    @("quarantine", "files\quarantine"),
    @("output", "files\output"),
    @("combined", "files\combined"),
    @("development\archived_sets", "files\archived_sets")
)

foreach ($Pair in $Pairs) {
    $Src = Join-Path $DataRoot $Pair[0]
    $Dst = Join-Path $DestRoot $Pair[1]
    if (-not (Test-Path $Src)) {
        New-Item -ItemType Directory -Path $Src -Force | Out-Null
    }
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null

    # 0-7 are success/informational robocopy codes; >=8 is a real failure.
    & robocopy $Src $Dst /E /XO /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Write-Error "robocopy failed for $Src -> $Dst (exit $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
}

# The database backup is a separate task; if it has not run yet, make one here
# so a snapshot folder always has a restorable DB.
$DbDest = Join-Path $DestRoot "db"
New-Item -ItemType Directory -Path $DbDest -Force | Out-Null
$BackupScript = Join-Path $PSScriptRoot "backup_db.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $BackupScript -RetentionDays 0 -BackupDir $DbDest
if ($LASTEXITCODE -ne 0) {
    Write-Error "database backup failed during development snapshot"
    exit $LASTEXITCODE
}

Write-Host "Development snapshot complete: $DestRoot" -ForegroundColor Green
