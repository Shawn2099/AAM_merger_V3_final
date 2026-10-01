<#
.SYNOPSIS
    scripts/uninstall_nssm_services.ps1 - Uninstalls AAM Merger V3 Windows Services via NSSM.
.DESCRIPTION
    Stops and cleanly removes:
      - AAMMerger-Web
      - AAMMerger-Worker
      - AAMMerger-PrefectServer (if present)
.PARAMETER NssmPath
    Path to nssm.exe. If omitted, checks PATH or tools\nssm\.
.EXAMPLE
    .\scripts\uninstall_nssm_services.ps1
#>
[CmdletBinding()]
param(
    [string]$NssmPath = ""
)

$ErrorActionPreference = "Stop"

# Administrator Privilege Check
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script requires Administrator privileges. Please re-run PowerShell as Administrator."
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

if ([string]::IsNullOrWhiteSpace($NssmPath)) {
    $cmdNssm = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmdNssm) {
        $NssmPath = $cmdNssm.Source
    } else {
        $localNssm = Join-Path $ProjectRoot "tools\nssm\win64\nssm.exe"
        if (-not (Test-Path $localNssm)) {
            $localNssm = Join-Path $ProjectRoot "tools\nssm\nssm.exe"
        }
        if (Test-Path $localNssm) {
            $NssmPath = $localNssm
        } else {
            Write-Error "Could not find nssm.exe. Please pass -NssmPath."
            exit 1
        }
    }
}
$NssmPath = (Resolve-Path $NssmPath).Path

$services = @("AAMMerger-Web", "AAMMerger-Worker", "AAMMerger-PrefectServer")

Write-Host "=== AAM Merger V3 - NSSM Service Uninstaller ===" -ForegroundColor Yellow
foreach ($svc in $services) {
    $existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Stopping service $svc..." -ForegroundColor Yellow
        & $NssmPath stop $svc 2>$null | Out-Null
        Start-Sleep -Seconds 1
        Write-Host "Removing service $svc..." -ForegroundColor Yellow
        & $NssmPath remove $svc confirm | Out-Null
        Write-Host "  Service $svc removed." -ForegroundColor Green
    } else {
        Write-Host "  Service $svc is not installed (skipping)." -ForegroundColor Gray
    }
}

Write-Host "`nAll AAM Merger services uninstalled successfully." -ForegroundColor Green
