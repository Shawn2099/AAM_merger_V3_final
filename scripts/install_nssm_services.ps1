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
      - Stdout / Stderr logging with automatic log rotation (10 MB per file)
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
.PARAMETER StartServices
    Switch to immediately start the services after installation.

.EXAMPLE
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
    [switch]$StartServices
)

$ErrorActionPreference = "Stop"

# 1. Administrator Privilege Check
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script requires Administrator privileges to install Windows Services. Please re-run PowerShell as Administrator."
    exit 1
}

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
                Write-Error "Please download nssm.exe manually, place it in '$toolsDir' or on PATH, and re-run."
                exit 1
            }
        }
    }
}
$NssmPath = (Resolve-Path $NssmPath).Path

Write-Host "=== AAM Merger V3 - NSSM Service Installer ===" -ForegroundColor Cyan
Write-Host "Project Directory : $ProjectDir"
Write-Host "Python Executable : $PythonExe"
Write-Host "NSSM Executable   : $NssmPath"
Write-Host "Binding           : ${HostAddress}:${Port}"
Write-Host "Prefect Server    : $(if ($WithPrefectServer) { 'Yes (Local Service)' } else { 'No (External or Worker-only)' })"

# 4. Ensure Data & Log Directories
$logsDir = Join-Path $ProjectDir "data\logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

$srcDir = Join-Path $ProjectDir "src"

function Install-Or-Update-NssmService {
    param(
        [string]$ServiceName,
        [string]$AppPath,
        [string]$AppArgs,
        [string]$AppDir,
        [string]$StdoutLog,
        [string]$StderrLog,
        [string]$Description
    )

    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Stopping existing service $ServiceName..." -ForegroundColor Yellow
        & $NssmPath stop $ServiceName 2>$null | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "Removing existing service $ServiceName..." -ForegroundColor Yellow
        & $NssmPath remove $ServiceName confirm | Out-Null
        Start-Sleep -Seconds 1
    }

    Write-Host "Installing service $ServiceName..." -ForegroundColor Cyan
    & $NssmPath install $ServiceName "$AppPath" "$AppArgs"
    & $NssmPath set $ServiceName AppDirectory "$AppDir"
    & $NssmPath set $ServiceName AppEnvironmentExtra "PYTHONPATH=$srcDir`nNO_PROXY=127.0.0.1,localhost,::1"
    & $NssmPath set $ServiceName AppStdout "$StdoutLog"
    & $NssmPath set $ServiceName AppStderr "$StderrLog"
    & $NssmPath set $ServiceName AppRotateFiles 1
    & $NssmPath set $ServiceName AppRotateOnline 1
    & $NssmPath set $ServiceName AppRotateBytes 10485760   # 10 MB per log file
    & $NssmPath set $ServiceName AppThrottle 2000         # Pause 2s before restarting after crash
    & $NssmPath set $ServiceName AppRestartDelay 2000
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START
    & $NssmPath set $ServiceName Description "$Description"

    Write-Host "  Service $ServiceName configured successfully." -ForegroundColor Green
}

# 5. Service 1: AAMMerger-Web
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

# 6. Service 2 (Optional): AAMMerger-PrefectServer
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

# 7. Service 3: AAMMerger-Worker
$workerStdout = Join-Path $logsDir "nssm-worker-stdout.log"
$workerStderr = Join-Path $logsDir "nssm-worker-stderr.log"
$workerArgs = "-m prefect worker start --pool aam-merger-process-pool"
Install-Or-Update-NssmService `
    -ServiceName "AAMMerger-Worker" `
    -AppPath $PythonExe `
    -AppArgs $workerArgs `
    -AppDir $ProjectDir `
    -StdoutLog $workerStdout `
    -StderrLog $workerStderr `
    -Description "AAM Merger V3 Prefect Process Pool Worker (Sync and VLM Extraction)"

# 8. Start Services if requested
if ($StartServices) {
    Write-Host "`nStarting services..." -ForegroundColor Cyan
    if ($WithPrefectServer) {
        & $NssmPath start AAMMerger-PrefectServer
        Write-Host "  Started AAMMerger-PrefectServer" -ForegroundColor Green
        Start-Sleep -Seconds 3
    }
    & $NssmPath start AAMMerger-Worker
    Write-Host "  Started AAMMerger-Worker" -ForegroundColor Green
    & $NssmPath start AAMMerger-Web
    Write-Host "  Started AAMMerger-Web" -ForegroundColor Green

    Start-Sleep -Seconds 2
    Write-Host "`nChecking Service Status:" -ForegroundColor Cyan
    Get-Service -Name "AAMMerger-*" | Format-Table -AutoSize
}

Write-Host "`n=== Installation Complete ===" -ForegroundColor Green
Write-Host "Manage with: .\scripts\manage_services.ps1 -Action [status|start|stop|restart]"
