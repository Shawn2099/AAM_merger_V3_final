<#
.SYNOPSIS
    Path allow-list for scripts/reset_data.ps1 (audit P2-7).

.DESCRIPTION
    reset_data.ps1 is destructive: it empties the folders named in config.yaml.
    A typo in that file (`output_folder: C:\`) must not be able to wipe a
    system folder, and the pre-reset backup just written into
    `data\backup` must never be deleted by the same run.

    Two rules, checked on fully-resolved paths (so `..` and short names
    cannot escape):

      1. The path must be inside the project's data root (`<project>\data`).
         A production deployment that stores data under `C:\AAM` must not use
         this test-rig script; use backup_db.py and a manual cleanup there.
      2. The path must not be the backup folder, or any parent of it.

    This file is dot-sourced by reset_data.ps1 and is exercised directly by
    tests/test_audit_scripts.py.
#>

function Get-NormalizedResetPath {
    <#
        Resolve a path without requiring it to exist, then drop trailing
        separators. Returns "" for an empty input.
    #>
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    $full = [System.IO.Path]::GetFullPath($Path)
    return $full.TrimEnd([char]'\', [char]'/')
}

function Test-PathWithinRoot {
    <#
        True when $Path is $Root or a descendant of $Root (case-insensitive,
        as Windows paths are). Both sides are normalised first.
    #>
    param(
        [string]$Path,
        [string]$Root
    )

    $p = Get-NormalizedResetPath $Path
    $r = Get-NormalizedResetPath $Root
    if ([string]::IsNullOrWhiteSpace($p) -or [string]::IsNullOrWhiteSpace($r)) {
        return $false
    }
    if ($p.Equals($r, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }

    $prefix = $r + [System.IO.Path]::DirectorySeparatorChar
    return $p.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-ResetPathAllowed {
    <#
        True only when the path is safe for reset_data.ps1 to clear:
        inside the data root and not the backup folder or one of its parents.
    #>
    param(
        [string]$Path,
        [string]$DataRoot,
        [string]$BackupDir
    )

    if (-not (Test-PathWithinRoot -Path $Path -Root $DataRoot)) { return $false }

    $p = Get-NormalizedResetPath $Path
    $b = Get-NormalizedResetPath $BackupDir
    if ([string]::IsNullOrWhiteSpace($b)) { return $true }

    # The backup folder itself, anything inside it, or a parent of it
    # (`data` or the project root configured as an output folder). Clearing
    # any of those would take the snapshot with it.
    if (Test-PathWithinRoot -Path $p -Root $b) { return $false }
    if (Test-PathWithinRoot -Path $b -Root $p) { return $false }

    return $true
}
