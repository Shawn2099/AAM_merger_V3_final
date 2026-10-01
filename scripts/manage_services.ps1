<#
.SYNOPSIS
    scripts/manage_services.ps1 - Operator management tool for AAM Merger V3 Windows Services.
.DESCRIPTION
    Provides simple, robust management (status, start, stop, restart) for all AAM Merger services.
.PARAMETER Action
    Action to perform: status (default), start, stop, restart.
.PARAMETER NssmPath
    Optional path to nssm.exe.
.EXAMPLE
    .\scripts\manage_services.ps1 -Action status
    .\scripts\manage_services.ps1 -Action restart
#>
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("status", "start", "stop", "restart")]
    [string]$Action = "status",

    [string]$NssmPath = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

# Locate NSSM if installed
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
        }
    }
}

$services = @("AAMMerger-Web", "AAMMerger-Worker", "AAMMerger-PrefectServer")

Write-Host "=== AAM Merger V3 - Service Manager [Action: $Action] ===" -ForegroundColor Cyan

switch ($Action) {
    "status" {
        $foundAny = $false
        foreach ($name in $services) {
            $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
            if ($svc) {
                $foundAny = $true
                $color = if ($svc.Status -eq "Running") { "Green" } else { "Yellow" }
                Write-Host ("[{0}] {1,-25} : {2}" -f (Get-Date -Format "HH:mm:ss"), $name, $svc.Status) -ForegroundColor $color
            }
        }
        if (-not $foundAny) {
            Write-Host "No AAMMerger services are currently installed. Run .\scripts\install_nssm_services.ps1 to install." -ForegroundColor Yellow
        }
    }

    "start" {
        foreach ($name in $services) {
            $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
            if ($svc) {
                if ($svc.Status -ne "Running") {
                    Write-Host "Starting $name..." -ForegroundColor Cyan
                    Start-Service -Name $name
                    Start-Sleep -Seconds 1
                    $updated = Get-Service -Name $name
                    Write-Host "  $name is now $($updated.Status)." -ForegroundColor Green
                } else {
                    Write-Host "  $name is already Running." -ForegroundColor Green
                }
            }
        }
    }

    "stop" {
        foreach ($name in $services) {
            $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
            if ($svc) {
                if ($svc.Status -ne "Stopped") {
                    Write-Host "Stopping $name..." -ForegroundColor Yellow
                    Stop-Service -Name $name -Force
                    Start-Sleep -Seconds 1
                    $updated = Get-Service -Name $name
                    Write-Host "  $name is now $($updated.Status)." -ForegroundColor Green
                } else {
                    Write-Host "  $name is already Stopped." -ForegroundColor Gray
                }
            }
        }
    }

    "restart" {
        foreach ($name in $services) {
            $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
            if ($svc) {
                Write-Host "Restarting $name..." -ForegroundColor Cyan
                Restart-Service -Name $name -Force
                Start-Sleep -Seconds 1
                $updated = Get-Service -Name $name
                Write-Host "  $name is now $($updated.Status)." -ForegroundColor Green
            }
        }
    }
}
