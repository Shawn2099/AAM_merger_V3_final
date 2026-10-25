<#
.SYNOPSIS
    scripts/install_nssm_services.ps1 - Installs and configures AAM Merger V3 Windows Services via NSSM.
.DESCRIPTION
    Configures two (or three) Windows Services:
      1. AAMMerger-Web: Uvicorn ASGI web server (FastAPI + Dashboard)
      2. AAMMerger-Worker: Prefect process pool worker (sync & extraction flows)
      3. AAMMerger-PrefectServer (Optional): Local Prefect orchestration server

    Each service is configured with:
      - Automatic restart on failure (AppThrottle 2000ms)
      - Stdout / Stderr logging; rotation is OFF so pilot logs are never pruned
      - Explicit working directory and PYTHONPATH environment setup
      - Auto-start on Windows boot (SERVICE_AUTO_START)

.PARAMETER ProjectDir
    Absolute path to project root. Defaults to script parent directory.
.PARAMETER PythonExe
    Path to python.exe (defaults to .venv\Scripts\python.exe in project root).
.PARAMETER NssmPath
    Path to nssm.exe. If omitted, the script checks PATH, tools\nssm\, or downloads NSSM 2.24.
.PARAMETER Port
    HTTP port for Uvicorn (default: 8000).
.PARAMETER HostAddress
    Bind address for Uvicorn (default: 0.0.0.0 for LAN access).
.PARAMETER WithPrefectServer
    Switch to also install and run a local self-hosted Prefect server as a Windows Service.
.PARAMETER PrefectApiUrl
    Prefect API the web app, worker and CLI talk to. Defaults to
    http://127.0.0.1:4200/api when -WithPrefectServer is used, otherwise the
    value is left to the machine's own PREFECT_API_URL.
.PARAMETER StartServices
    Switch to immediately start the services after installation.
.PARAMETER ConfigPath
    config.yaml to validate and to hand to the services (AAM_CONFIG_PATH).
    Defaults to <ProjectDir>\config.yaml. Meant for validation and -DryRun tests only: a
    real install REFUSES any other file, because scripts\deploy_prefect.py and the backup
    task always read <ProjectDir>\config.yaml.
.PARAMETER AllowAnyHost
    By default the installer REFUSES (exit 1, before any service is created) when
    config.yaml server.allowed_hosts is missing, empty or contains "*". This switch
    downgrades that to a loud warning. For a lab install only, never the office PC.
.PARAMETER DryRun
    Print every action the installer WOULD take and change nothing: no nssm call that
    modifies anything, no directory or file created, no download, no admin needed. The
    allowed_hosts gate is a plain read of the YAML (no config validation, no folders
    created); a real install also validates the whole file with the application's loader.

Service order: the worker DEPENDS on AAMMerger-PrefectServer (nssm DependOnService), so
Windows starts Prefect first and stops the worker first. The web service does NOT depend on
Prefect: the dashboard must start even when Prefect fails at boot (the poller backs off while
Prefect is down).
Re-running the script removes and re-creates each service, so every setting is
refreshed (idempotent).

.EXAMPLE
    .\scripts\install_nssm_services.ps1 -WithPrefectServer -DryRun
    .\scripts\install_nssm_services.ps1
    .\scripts\install_nssm_services.ps1 -WithPrefectServer -StartServices
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = "",
    [string]$PythonExe = "",
    [string]$NssmPath = "",
    [string]$Port = "8000",
    [string]$HostAddress = "0.0.0.0",
    [switch]$WithPrefectServer,
    [string]$PrefectApiUrl = "",
    [string]$WorkPoolName = "aam-merger-process-pool",
    [switch]$StartServices,
    [string]$ConfigPath = "",
    [switch]$AllowAnyHost,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# 2. Determine Paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ProjectDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
}
$ProjectDir = (Resolve-Path $ProjectDir).Path

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $sysPython = (Get-Command python -ErrorAction SilentlyContinue).Source
        if ($sysPython) {
            $PythonExe = $sysPython
        } else {
            Write-Error "Could not find python.exe. Please specify -PythonExe or create a .venv virtual environment."
            exit 1
        }
    }
}
$PythonExe = (Resolve-Path $PythonExe).Path
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ProjectDir "config.yaml"
}

function Write-Plan {
    param([string]$Text)
    Write-Host "[DRY-RUN] $Text" -ForegroundColor Magenta
}

# 2b. server.allowed_hosts must be pinned (DELIVERY.md section 2). Checked BEFORE
# anything is created. The gate is a plain YAML read (PyYAML is a project
# dependency) so a dry run or a refusal has no side effects; the application's
# own loader creates folders, so it only runs on a real install (below).
if (-not (Test-Path $ConfigPath)) {
    Write-Host "ERROR: config.yaml not found at $ConfigPath. Copy config.example.yaml and edit it first." -ForegroundColor Red
    exit 1
}
$ConfigPath = (Resolve-Path $ConfigPath).Path
$defaultConfig = Join-Path $ProjectDir "config.yaml"
if ((-not $DryRun) -and ($ConfigPath -ne $defaultConfig)) {
    Write-Host "ERROR: -ConfigPath ($ConfigPath) differs from $defaultConfig." -ForegroundColor Red
    Write-Host "-ConfigPath is for validation / -DryRun only: deploy_prefect.py and the backup task read $defaultConfig. Nothing was installed." -ForegroundColor Red
    exit 1
}
$srcForReader = Join-Path $ProjectDir "src"
$hostsReader = @"
import json, sys
try:
    import yaml
except ImportError:
    print('PyYAML is not installed in this Python', file=sys.stderr)
    sys.exit(3)
with open(r'$ConfigPath', encoding='utf-8') as fh:
    data = yaml.safe_load(fh) or {}
hosts = (data.get('server') or {}).get('allowed_hosts')
print(json.dumps(list(hosts) if isinstance(hosts, list) else []))
"@
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$hostsRaw = & $PythonExe -c $hostsReader 2>&1
$readerCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
$hostsErr = @($hostsRaw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })
$hostsOut = @($hostsRaw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })
if ($readerCode -ne 0 -or $hostsOut.Count -eq 0) {
    Write-Host "ERROR: could not read server.allowed_hosts from $ConfigPath (config invalid?)." -ForegroundColor Red
    foreach ($e in $hostsErr) { Write-Host "  $($e.ToString())" -ForegroundColor Red }
    exit 1
}
$hostsParsed = "$($hostsOut[-1])" | ConvertFrom-Json
# Windows PowerShell 5.1 hands back the JSON array as ONE object; enumerate it.
$hostsList = @($hostsParsed | ForEach-Object { $_ })
$hostsUnsafe = ($hostsList.Count -eq 0) -or ($hostsList -contains "*")
if ($hostsUnsafe) {
    $msg = "server.allowed_hosts in $ConfigPath is missing or contains '*' (accepts any Host header). Set it to this PC's name and static IP, e.g. [`"aam-server`", `"10.0.0.5`"]."
    if ($AllowAnyHost) {
        Write-Host "WARNING: $msg" -ForegroundColor Red
        Write-Host "WARNING: continuing only because -AllowAnyHost was given. Do NOT use this on the office PC." -ForegroundColor Red
    } else {
        Write-Host "ERROR: $msg" -ForegroundColor Red
        Write-Host "Nothing was installed. (Lab installs only: re-run with -AllowAnyHost.)" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "allowed_hosts OK: $($hostsList -join ', ')" -ForegroundColor Green
}

# 1. Administrator Privilege Check (not needed for a dry run)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($DryRun) {
        Write-Plan "not running as Administrator; a real install would stop here"
    } else {
        Write-Host "ERROR: This script requires Administrator privileges to install Windows Services. Please re-run PowerShell as Administrator." -ForegroundColor Red
        exit 1
    }
}

# 2c. Real install only: validate the whole file exactly as the app will (this
# loader creates the data folders, which is why a dry run skips it).
if (-not $DryRun) {
    $fullReader = @"
import sys
sys.path.insert(0, r'$srcForReader')
from app.core.config import load_config
load_config(r'$ConfigPath')
"@
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $fullRaw = & $PythonExe -c $fullReader 2>&1
    $fullCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($fullCode -ne 0) {
        Write-Host "ERROR: $ConfigPath failed validation by the application's config loader:" -ForegroundColor Red
        foreach ($l in @($fullRaw)) { Write-Host "  $($l.ToString())" -ForegroundColor Red }
        exit 1
    }
}

# 3. Locate or Download NSSM
if ([string]::IsNullOrWhiteSpace($NssmPath)) {
    $cmdNssm = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmdNssm) {
        $NssmPath = $cmdNssm.Source
    } else {
        $localNssm = Join-Path $ProjectDir "tools\nssm\win64\nssm.exe"
        if (-not (Test-Path $localNssm)) {
            $localNssm = Join-Path $ProjectDir "tools\nssm\nssm.exe"
        }
        if (Test-Path $localNssm) {
            $NssmPath = $localNssm
        } elseif ($DryRun) {
            Write-Plan "NSSM not found; a real install would download NSSM 2.24 into tools\nssm"
            $NssmPath = "nssm.exe"
        } else {
            Write-Host "NSSM not found on system PATH or in tools\nssm. Downloading NSSM 2.24..." -ForegroundColor Yellow
            $toolsDir = Join-Path $ProjectDir "tools\nssm"
            if (-not (Test-Path $toolsDir)) {
                New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
            }
            $zipPath = Join-Path $toolsDir "nssm-2.24.zip"
            $url = "https://nssm.cc/release/nssm-2.24.zip"
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            try {
                Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
                Expand-Archive -Path $zipPath -DestinationPath $toolsDir -Force
                $extractedNssm = Join-Path $toolsDir "nssm-2.24\win64\nssm.exe"
                if (Test-Path $extractedNssm) {
                    $NssmPath = $extractedNssm
                }
            } catch {
                Write-Warning "Direct download from nssm.cc failed ($($_.Exception.Message))."
                $targetNssm = Join-Path $toolsDir "nssm.exe"
                Write-Error "Download nssm-2.24.zip from https://nssm.cc/release/nssm-2.24.zip on a PC with internet, extract win64\nssm.exe to $targetNssm, and re-run."
                exit 1
            }
        }
    }
}
if (-not $DryRun) {
    $NssmPath = (Resolve-Path $NssmPath).Path
}

function Invoke-Nssm {
    # Single choke point for every nssm call: a dry run only prints it.
    if ($DryRun) {
        Write-Plan "nssm $($args -join ' ')"
        return
    }
    & $NssmPath @args
    if ($LASTEXITCODE -ne 0 -and $args[0] -ne 'stop') {
        Write-Host "ERROR: nssm $($args -join ' ') failed ($LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

Write-Host "=== AAM Merger V3 - NSSM Service Installer ===" -ForegroundColor Cyan
Write-Host "Project Directory : $ProjectDir"
Write-Host "Python Executable : $PythonExe"
Write-Host "NSSM Executable   : $NssmPath"
Write-Host "Binding           : ${HostAddress}:${Port}"
Write-Host "Work Pool         : $WorkPoolName"

# Every process must talk to the same Prefect API. Without PREFECT_API_URL each
# one starts its own ephemeral server, so the worker never sees the scheduled
# deployment and the midnight sync silently never runs. Resolve it BEFORE the
# banner so the banner reports the URL this install will actually use.
$prefectApi = $PrefectApiUrl
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $WithPrefectServer) {
    $prefectApi = "http://127.0.0.1:4200/api"
}
Write-Host "Prefect Server    : $(if ($WithPrefectServer) { 'Yes (Local Service)' } else { 'No (External or Worker-only)' })"
Write-Host "Prefect API URL   : $(if ($prefectApi) { $prefectApi } else { '(MISSING - worker will not start)' })"

# 4. Ensure Data & Log Directories
$logsDir = Join-Path $ProjectDir "data\logs"
if ($DryRun) {
    Write-Plan "ensure log directory $logsDir"
} elseif (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

$srcDir = Join-Path $ProjectDir "src"

$prefectHome = Join-Path $ProjectDir "data\prefect"
$extraEnv = "PYTHONPATH=$srcDir`nNO_PROXY=127.0.0.1,localhost,::1`nPYTHONUTF8=1`nPYTHONIOENCODING=utf-8`nPREFECT_HOME=$prefectHome`nAAM_CONFIG_PATH=$ConfigPath"
if (-not [string]::IsNullOrWhiteSpace($prefectApi)) {
    $extraEnv = "$extraEnv`nPREFECT_API_URL=$prefectApi"
}

function Install-Or-Update-NssmService {
    param(
        [string]$ServiceName,
        [string]$AppPath,
        [string]$AppArgs,
        [string]$AppDir,
        [string]$StdoutLog,
        [string]$StderrLog,
        [string]$Description,
        [string[]]$DependsOn = @()
    )

    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Stopping existing service $ServiceName..." -ForegroundColor Yellow
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        Invoke-Nssm stop $ServiceName 2>$null | Out-Null
        $ErrorActionPreference = $prevEap
        if (-not $DryRun) { Start-Sleep -Seconds 2 }
        Write-Host "Removing existing service $ServiceName..." -ForegroundColor Yellow
        Invoke-Nssm remove $ServiceName confirm | Out-Null
        if (-not $DryRun) { Start-Sleep -Seconds 1 }
    }

    Write-Host "Installing service $ServiceName..." -ForegroundColor Cyan
    Invoke-Nssm install $ServiceName "$AppPath" "$AppArgs"
    Invoke-Nssm set $ServiceName AppDirectory "$AppDir"
    Invoke-Nssm set $ServiceName AppEnvironmentExtra "$extraEnv"
    Invoke-Nssm set $ServiceName AppStdout "$StdoutLog"
    Invoke-Nssm set $ServiceName AppStderr "$StderrLog"
    # Pilot rule: logs are collected, never rotated away. The development
    # snapshot task copies them; NSSM keeps one growing file per service.
    Invoke-Nssm set $ServiceName AppRotateFiles 0
    Invoke-Nssm set $ServiceName AppRotateOnline 0
    Invoke-Nssm set $ServiceName AppRotateBytes 0
    Invoke-Nssm set $ServiceName AppThrottle 2000         # Pause 2s before restarting after crash
    Invoke-Nssm set $ServiceName AppRestartDelay 2000
    Invoke-Nssm set $ServiceName Start SERVICE_AUTO_START
    Invoke-Nssm set $ServiceName Description "$Description"
    if ($DependsOn.Count -gt 0) {
        # Windows starts the dependency first and stops this service first.
        Invoke-Nssm set $ServiceName DependOnService @DependsOn
        Write-Host "  Depends on: $($DependsOn -join ', ')" -ForegroundColor Gray
    }

    Write-Host "  Service $ServiceName configured successfully." -ForegroundColor Green
}

# 4b. Stop any installed AAM service dependents-first (web, worker, and Prefect when
# it is part of this install) so a
# re-run never tries to remove a service that others still depend on.
$stopNames = @("AAMMerger-Web", "AAMMerger-Worker")
if ($WithPrefectServer) { $stopNames += "AAMMerger-PrefectServer" }
foreach ($stopName in $stopNames) {
    if (Get-Service -Name $stopName -ErrorAction SilentlyContinue) {
        Write-Host "Stopping existing service $stopName (dependents first)..." -ForegroundColor Yellow
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        Invoke-Nssm stop $stopName 2>$null | Out-Null
        $ErrorActionPreference = $prevEap
    }
}

if (-not $WithPrefectServer) {
    Write-Host "No service dependency (external Prefect): web and worker do not wait for a local Prefect server." -ForegroundColor Yellow
}

# 5. Service (Optional): AAMMerger-PrefectServer
if ($WithPrefectServer) {
    $serverStdout = Join-Path $logsDir "nssm-prefect-server-stdout.log"
    $serverStderr = Join-Path $logsDir "nssm-prefect-server-stderr.log"
    $serverArgs = "-m prefect server start --host 127.0.0.1 --port 4200"
    Install-Or-Update-NssmService `
        -ServiceName "AAMMerger-PrefectServer" `
        -AppPath $PythonExe `
        -AppArgs $serverArgs `
        -AppDir $ProjectDir `
        -StdoutLog $serverStdout `
        -StderrLog $serverStderr `
        -Description "AAM Merger V3 Local Prefect Orchestration Server"
}

# 6. Service: AAMMerger-Worker (depends on the Prefect server when installed here)
$prefectDep = @()
if ($WithPrefectServer) { $prefectDep = @("AAMMerger-PrefectServer") }
$workerStdout = Join-Path $logsDir "nssm-worker-stdout.log"
$workerStderr = Join-Path $logsDir "nssm-worker-stderr.log"
$workerArgs = "-m prefect worker start --pool $WorkPoolName"
Install-Or-Update-NssmService `
    -ServiceName "AAMMerger-Worker" `
    -AppPath $PythonExe `
    -AppArgs $workerArgs `
    -AppDir $ProjectDir `
    -StdoutLog $workerStdout `
    -StderrLog $workerStderr `
    -Description "AAM Merger V3 Prefect Process Pool Worker (Sync and VLM Extraction)" `
    -DependsOn $prefectDep

# 7. Service: AAMMerger-Web. No DependOnService here, on purpose: the dashboard starts even
#    when the Prefect server fails at boot. Only the worker waits for Prefect.
$webStdout = Join-Path $logsDir "nssm-web-stdout.log"
$webStderr = Join-Path $logsDir "nssm-web-stderr.log"
$webArgs = "-m uvicorn app.main:app --host $HostAddress --port $Port"
Install-Or-Update-NssmService `
    -ServiceName "AAMMerger-Web" `
    -AppPath $PythonExe `
    -AppArgs $webArgs `
    -AppDir $ProjectDir `
    -StdoutLog $webStdout `
    -StderrLog $webStderr `
    -Description "AAM Merger V3 FastAPI Web Application and Operator Dashboard"

# 8. Start Services if requested
if ($StartServices) {
    Write-Host "`nStarting services..." -ForegroundColor Cyan
    if ($WithPrefectServer) {
        Invoke-Nssm start AAMMerger-PrefectServer
        Write-Host "  Started AAMMerger-PrefectServer" -ForegroundColor Green
        if (-not $DryRun) { Start-Sleep -Seconds 3 }
    }
    Invoke-Nssm start AAMMerger-Worker
    Write-Host "  Started AAMMerger-Worker" -ForegroundColor Green
    Invoke-Nssm start AAMMerger-Web
    Write-Host "  Started AAMMerger-Web" -ForegroundColor Green

    if (-not $DryRun) {
        Start-Sleep -Seconds 2
        Write-Host "`nChecking Service Status:" -ForegroundColor Cyan
        Get-Service -Name "AAMMerger-*" | Format-Table -AutoSize
    }
}

if ($DryRun) {
    Write-Host "`n=== Dry run complete: nothing was changed ===" -ForegroundColor Magenta
    exit 0
}
Write-Host "`n=== Installation Complete ===" -ForegroundColor Green
Write-Host "Manage with: .\scripts\manage_services.ps1 -Action [status|start|stop|restart]"
exit 0
