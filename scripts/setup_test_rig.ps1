<#
.SYNOPSIS
    scripts/setup_test_rig.ps1 - Complete one-click deployment & test rig setup for AAM Merger V3.
.DESCRIPTION
    Automates the entire setup of AAM Merger V3 on a Windows Server 2016 or Windows test rig:
      1. Validates Administrator privileges.
      2. Ensures Python environment (.venv) and dependencies are installed.
      3. Verifies or generates config.yaml from config.example.yaml.
      4. Checks for .env / OPENROUTER_API_KEY.
      5. Creates required data, log, and backup directories.
      6. Applies database schema migrations (alembic upgrade head).
      7. (Optional) Configures Windows Firewall inbound rule for web access.
      8. (Optional) Configures Windows Scheduled Task for daily SQLite WAL backups.
      9. Downloads NSSM (if missing) and installs AAM Merger Windows Services.
     10. Starts services and validates /health endpoint.

.PARAMETER Port
    HTTP Port for Uvicorn web service (default: 8000).
.PARAMETER HostAddress
    Bind address for Uvicorn (default: 0.0.0.0).
.PARAMETER OpenFirewall
    Switch to automatically add a Windows Firewall inbound rule for the web port.
.PARAMETER ScheduleBackup
    Switch to register a daily Windows Scheduled Task for SQLite backups (11:30 AM).
.PARAMETER WithPrefectServer
    Switch to install a local self-hosted Prefect server as a Windows Service.
.PARAMETER PrefectApiUrl
    Prefect API URL for the web app, worker and CLI. Defaults to
    http://127.0.0.1:4200/api when -WithPrefectServer is used. Point it at an
    external Prefect server or Prefect Cloud instead if you have one.
.PARAMETER AllowAnyHost
    Passed to the service installer: lets it continue when config.yaml
    server.allowed_hosts is missing or ["*"]. Lab machines only.
.PARAMETER SkipServices
    Switch to configure files, database, and schedule without installing NSSM services.

.EXAMPLE
    .\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
#>
[CmdletBinding()]
param(
    [string]$Port = "8000",
    [string]$HostAddress = "0.0.0.0",
    [switch]$OpenFirewall,
    [switch]$ScheduleBackup,
    [switch]$WithPrefectServer,
    [string]$PrefectApiUrl = "",
    [switch]$AllowAnyHost,
    [switch]$SkipServices
)

$ErrorActionPreference = "Stop"

# Prefect's console renderer draws box characters. A Windows console defaults
# to cp1252, so the CLI did its work and then crashed while printing the
# result — "Work pool create" exited 1 even though the pool existed. UTF-8 mode
# makes the exit code tell the truth.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "       AAM Merger V3 - Production / Test Rig Setup          " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Administrator check.
# Files, config and migrations need no elevation; services, firewall and the
# backup task do. Requiring admin for a files-only setup blocked a non-admin
# operator from preparing a machine at all.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$needsAdmin = $OpenFirewall -or $ScheduleBackup -or (-not $SkipServices)
if ($needsAdmin -and -not $isAdmin) {
    Write-Error "Administrator privileges are required to configure services, firewall, and tasks. Re-run PowerShell as Administrator, or pass -SkipServices for a files-only setup."
    exit 1
}
if (-not $isAdmin) {
    Write-Warning "Not running as Administrator: installing files, config and database only. Services, firewall and the backup task are skipped."
}

# Both steps below only warn: neither may stop the install.
if ($isAdmin) {
    # A sleeping PC suspends the poller, the midnight run and LAN access.
    try {
        powercfg /change standby-timeout-ac 0 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "powercfg standby-timeout-ac exited $LASTEXITCODE" }
        powercfg /change hibernate-timeout-ac 0 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "powercfg hibernate-timeout-ac exited $LASTEXITCODE" }
        Write-Host "Sleep on AC power disabled so the service keeps running" -ForegroundColor Green
    } catch {
        Write-Warning "Could not disable sleep on AC power ($($_.Exception.Message)). Set the power plan to never sleep by hand."
    }
}

# The PDF reader (PyMuPDF) needs the Visual C++ runtime.
try {
    $vcRuntimeKey = "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    $vcInstalled = $null
    if (Test-Path $vcRuntimeKey) {
        $vcInstalled = (Get-ItemProperty -Path $vcRuntimeKey -Name Installed -ErrorAction Stop).Installed
    }
    if ($vcInstalled -ne 1) {
        Write-Host "Microsoft Visual C++ 2015-2022 x64 runtime not found; PDF reading (PyMuPDF) may fail. Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and re-run." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Microsoft Visual C++ 2015-2022 x64 runtime not found; PDF reading (PyMuPDF) may fail. Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and re-run." -ForegroundColor Yellow
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

Write-Host "[1/8] Verifying Project Root and Python Environment..." -ForegroundColor Cyan
Write-Host "  Project Root: $ProjectRoot"

# Re-run safety: a running service keeps the SQLite file and the .pyd files of .venv open, so the
# sync and the migration below must not run beside it. Stop them first. The installer's start step
# starts them again; with -SkipServices they are started again before step 7.
$stoppedServices = @()
if ($isAdmin) {
    foreach ($svcName in @("AAMMerger-Web", "AAMMerger-Worker", "AAMMerger-PrefectServer")) {
        $svcObj = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($svcObj -and $svcObj.Status -ne "Stopped") {
            try {
                Stop-Service -Name $svcName -Force -ErrorAction Stop
                $stoppedServices += $svcName
                Write-Host "  Stopped $svcName for the update." -ForegroundColor Yellow
            } catch {
                Write-Warning "Could not stop $svcName ($($_.Exception.Message)). Stop it by hand before the update."
            }
        }
    }
}

# Find uv or python
$hasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not $hasUv) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Error "Neither 'uv' nor 'python' is on PATH. Install Python 3.11+ (and optionally uv) from python.org, then re-run."
        exit 1
    }
    # The Microsoft Store alias passes Get-Command but is not Python: it exits non-zero here.
    # Native stderr must not become a terminating error while we probe.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & python -c "import sys" > $null 2>&1
    $pythonWorks = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if (-not $pythonWorks) {
        Write-Error "Python is the Microsoft Store placeholder; install Python 3.11+ from python.org or install uv."
        exit 1
    }
}

if ($hasUv) {
    # Idempotent: creates .venv if missing and repairs a stale one, so a
    # re-run after pulling an update does not silently keep old dependencies.
    # Deployment machines do not need the test tooling; a development machine
    # should run `uv sync --all-groups` afterwards.
    Write-Host "  Syncing dependencies with uv..."
    uv sync --no-dev
    if ($LASTEXITCODE -ne 0) {
        Write-Error "uv sync failed."
        exit $LASTEXITCODE
    }
} else {
    if (-not (Test-Path $venvPython)) {
        Write-Host "  .venv not found. Creating virtual environment..." -ForegroundColor Yellow
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Could not create .venv. Install Python 3.11+ and re-run, or install uv."
            exit $LASTEXITCODE
        }
        & $venvPython -m pip install --upgrade pip
    }
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dependency installation failed."
        exit $LASTEXITCODE
    }
}
if (-not (Test-Path $venvPython)) {
    Write-Error "Python environment missing at $venvPython."
    exit 1
}
# PyMuPDF is a compiled wheel that loads the Visual C++ runtime. Fail here, with the fix,
# rather than on the first PDF. Native stderr is kept out of the terminating-error path.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $venvPython -c "import pymupdf" > $null 2>&1
$pymupdfWorks = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if (-not $pymupdfWorks) {
    Write-Error "PyMuPDF will not load (the PDF reader). Install the Microsoft Visual C++ 2015-2022 x64 runtime from https://aka.ms/vs/17/release/vc_redist.x64.exe, then re-run."
    exit 1
}
& $venvPython -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Python older than 3.12: the copy-safety check cannot see file creation times, so files being extracted may be read early. Use Python 3.12 or newer (uv installs it automatically)." -ForegroundColor Yellow
}
Write-Host "  Python: $venvPython" -ForegroundColor Green

# 2. Config & Folders
Write-Host "[2/8] Checking Configuration & Data Directories..." -ForegroundColor Cyan
$configPath = Join-Path $ProjectRoot "config.yaml"
if (-not (Test-Path $configPath)) {
    Write-Host "  config.yaml missing. Copying config.example.yaml..." -ForegroundColor Yellow
    Copy-Item (Join-Path $ProjectRoot "config.example.yaml") $configPath
    Write-Host "  Created config.yaml (please review paths for production)." -ForegroundColor Yellow
    # A fresh config must not be the open ["*"] list the installer refuses: allow this machine
    # by name and by its current LAN address(es). If the address changes (DHCP), add the new one
    # on the Fixed-by-IT list in config.yaml, or give the PC a static address.
    $hosts = @("localhost", "127.0.0.1", $env:COMPUTERNAME.ToLower())
    $hosts += @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        ForEach-Object { $_.IPAddress })
    $hostList = "[" + (($hosts | Select-Object -Unique | ForEach-Object { "`"$_`"" }) -join ", ") + "]"
    $raw = Get-Content -Raw $configPath
    $raw = $raw -replace '(?m)^(\s*)allowed_hosts:\s*\["\*"\].*$', ("`$1allowed_hosts: " + $hostList)
    Set-Content -Path $configPath -Value $raw -NoNewline -Encoding UTF8
    Write-Host "  Allowed hosts set to $hostList" -ForegroundColor Green
} else {
    Write-Host "  config.yaml present." -ForegroundColor Green
}

$envPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envPath)) {
    if (-not $env:OPENROUTER_API_KEY) {
        Write-Warning "  .env file with OPENROUTER_API_KEY is missing! VLM extraction requires an API key."
    }
} else {
    Write-Host "  .env file present." -ForegroundColor Green
}

$dataDirs = @(
    "data\input",
    "data\output",
    "data\quarantine",
    "data\stored",
    "data\combined",
    "data\logs",
    "data\backup",
    "data\samples",
    "data\raw_extractions",
    "data\development\archived_sets",
    "data\development\snapshots\db",
    "data\prefect"
)
foreach ($dir in $dataDirs) {
    $fullPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
}
Write-Host "  Data directories verified." -ForegroundColor Green

# 3. Database Migration
# NOT a bare `alembic upgrade head`: the live DB was created by create_all and
# has no alembic_version, so Alembic would start from base and fail on the
# existing tables. migrate_db.py stamps the proven revision first, and refuses
# to report success unless the columns the code needs are present.
Write-Host "[3/8] Applying SQLite Database Migrations..." -ForegroundColor Cyan
& $venvPython "scripts\migrate_db.py"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Database migration failed."
    exit $LASTEXITCODE
}
Write-Host "  Database schema up to date." -ForegroundColor Green

# 4. Optional Firewall Rule
if ($OpenFirewall) {
    Write-Host "[4/8] Configuring Windows Firewall..." -ForegroundColor Cyan
    # The rule below allows the Domain and Private profiles only. On a Public network other PCs
    # cannot connect, so say so now.
    $netProfiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)
    foreach ($netProfile in $netProfiles) {
        Write-Host "  Network '$($netProfile.Name)' profile: $($netProfile.NetworkCategory)"
    }
    if ($netProfiles | Where-Object { $_.NetworkCategory -eq "Public" }) {
        Write-Host "  WARNING: the network is Public. Other PCs cannot connect to the dashboard until it is set to Private (Settings > Network & Internet > the network > Private network)." -ForegroundColor Yellow
    }
    $ruleName = "AAM Merger V3 Web ($Port)"
    $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existingRule) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
        Write-Host "  Firewall rule created for TCP port $Port." -ForegroundColor Green
    } else {
        Write-Host "  Firewall rule '$ruleName' already exists." -ForegroundColor Green
    }
} else {
    Write-Host "[4/8] Skipping Windows Firewall configuration (-OpenFirewall not set)." -ForegroundColor Gray
}

# 5. Optional Scheduled Task for Backups
if ($ScheduleBackup) {
    Write-Host "[5/8] Configuring Scheduled Task for Daily Database Backups..." -ForegroundColor Cyan
    $taskName = "AAMMerger-DailyBackup"
    $backupScript = Join-Path $ProjectRoot "scripts\backup_db.ps1"
    $backupDir = Join-Path $ProjectRoot "data\development\snapshots\db"
    $backupArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`" -RetentionDays 0 -BackupDir `"$backupDir`""
    $action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $backupArgs -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At 11:30am
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "  Registered scheduled task '$taskName' at 11:30 AM daily (no pruning)." -ForegroundColor Green

    # The development snapshot runs after the DB backup and copies logs/files.
    $snapshotTask = "AAMMerger-DevelopmentSnapshot"
    $snapshotScript = Join-Path $ProjectRoot "scripts\development_snapshot.ps1"
    $snapshotArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$snapshotScript`""
    $snapshotAction = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $snapshotArgs -WorkingDirectory $ProjectRoot
    $snapshotTrigger = New-ScheduledTaskTrigger -Daily -At 12:00pm
    Unregister-ScheduledTask -TaskName $snapshotTask -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $snapshotTask -Action $snapshotAction -Trigger $snapshotTrigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "  Registered scheduled task '$snapshotTask' at 12:00 PM daily (copy-only)." -ForegroundColor Green
} else {
    Write-Host "[5/8] Skipping Scheduled Backup Task registration (-ScheduleBackup not set)." -ForegroundColor Gray
}

# Prefect state belongs with the pilot data, not in the service account profile.
$env:PREFECT_HOME = Join-Path $ProjectRoot "data\prefect"
if (-not (Test-Path $env:PREFECT_HOME)) {
    New-Item -ItemType Directory -Path $env:PREFECT_HOME -Force | Out-Null
}

# 6. Install NSSM Services (must exist before the Prefect pool/deployment is
#    registered: the worker and the CLI have to point at the same API).
#
# Resolve the API URL BEFORE installing anything. `prefect worker start` exits
# immediately when PREFECT_API_URL is unset ("must be set to start a Worker"),
# and NSSM restarts it every 2 s forever. Installing the worker without an API
# produced exactly that crash loop on the documented install command.
$prefectApi = $PrefectApiUrl
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $WithPrefectServer) {
    $prefectApi = "http://127.0.0.1:4200/api"
}
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $env:PREFECT_API_URL) {
    $prefectApi = $env:PREFECT_API_URL
}
if (-not $SkipServices -and [string]::IsNullOrWhiteSpace($prefectApi)) {
    Write-Error "Refusing to install the worker service: no Prefect API URL. Re-run with -WithPrefectServer (single host) or -PrefectApiUrl <url>; -SkipServices installs only the web app."
    exit 1
}

# The work-pool name comes from config.yaml. Compute it before installing: the
# worker command used to hardcode "aam-merger-process-pool" while setup created
# the pool named in config, so changing the key silently left the worker polling
# a pool nobody created.
$poolName = (& $venvPython -c "import sys; sys.path.insert(0,'src'); from app.core.config import load_config; print(load_config().prefect.work_pool_name)").Trim()
if ([string]::IsNullOrWhiteSpace($poolName)) {
    Write-Error "prefect.work_pool_name is blank in config.yaml."
    exit 1
}

if (-not $SkipServices) {
    Write-Host "[6/8] Installing Windows Services via NSSM..." -ForegroundColor Cyan
    $installScript = Join-Path $ProjectRoot "scripts\install_nssm_services.ps1"
    $installParams = @{
        ProjectDir        = $ProjectRoot
        PythonExe         = $venvPython
        Port              = $Port
        HostAddress       = $HostAddress
        StartServices     = $true
        WorkPoolName      = $poolName
    }
    if ($WithPrefectServer) {
        $installParams["WithPrefectServer"] = $true
    }
    if (-not [string]::IsNullOrWhiteSpace($prefectApi)) {
        # Always propagate the resolved URL, not only when -PrefectApiUrl was
        # passed explicitly: the worker and the CLI must talk to the same API.
        $installParams["PrefectApiUrl"] = $prefectApi
    }
    if ($AllowAnyHost) { $installParams["AllowAnyHost"] = $true }
    & $installScript @installParams
    # An exit inside a script called with & never throws, so check it here.
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Service install refused/failed (exit $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
} else {
    Write-Host "[6/8] Skipping NSSM service installation (-SkipServices specified)." -ForegroundColor Gray
}

# Services stopped for the update come back here when no install ran (-SkipServices), Prefect first.
if ($SkipServices) {
    for ($k = $stoppedServices.Count - 1; $k -ge 0; $k--) {
        try {
            Start-Service -Name $stoppedServices[$k] -ErrorAction Stop
            Write-Host "  Started $($stoppedServices[$k]) again." -ForegroundColor Green
        } catch {
            Write-Warning "Could not start $($stoppedServices[$k]) ($($_.Exception.Message)). Start it by hand."
        }
    }
}

# 7. Prefect: create the work pool and register the midnight deployment.
#    The worker command names a pool, but nothing created it: on a fresh host
#    `deploy_prefect.py` failed with "Work pool not found" and the nightly sync
#    was never scheduled. An API must be reachable for any of this to mean
#    anything, so say so plainly instead of registering into a throwaway server.
Write-Host "[7/8] Configuring Prefect (work pool + midnight deployment)..." -ForegroundColor Cyan
$prefectApi = $PrefectApiUrl
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $WithPrefectServer) {
    $prefectApi = "http://127.0.0.1:4200/api"
}
if ([string]::IsNullOrWhiteSpace($prefectApi) -and $env:PREFECT_API_URL) {
    $prefectApi = $env:PREFECT_API_URL
}

$apiUp = $false
if (-not [string]::IsNullOrWhiteSpace($prefectApi)) {
    $env:PREFECT_API_URL = $prefectApi
    Write-Host "  Prefect API: $prefectApi"
    $healthUrl = $prefectApi.TrimEnd('/') + "/health"
    # A first Prefect start (database migrations) on a slow PC can take well over 30 s.
    for ($i = 1; $i -le 120; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $apiUp = $true; break }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $apiUp) {
        Write-Host "Prefect API at $prefectApi did not answer within 120s." -ForegroundColor Red
    }
}

$prefectRegistered = $false
if ($apiUp) {
    & $venvPython -m prefect work-pool create $poolName --type process --overwrite
    $poolOk = ($LASTEXITCODE -eq 0)
    if (-not $poolOk) {
        Write-Warning "Could not create work pool '$poolName'. Check the Prefect server log."
    } else {
        Write-Host "  Work pool '$poolName' ready." -ForegroundColor Green
    }

    $deployScript = Join-Path $ProjectRoot "scripts\deploy_prefect.py"
    & $venvPython $deployScript
    $deployOk = ($LASTEXITCODE -eq 0)
    if (-not $deployOk) {
        Write-Warning "Prefect deployment registration failed. The scheduled sync will not run until this succeeds."
    }
    $prefectRegistered = $poolOk -and $deployOk

    # The worker may have started before the pool existed; restart it so it
    # picks up the pool and the deployment.
    if (-not $SkipServices) {
        $worker = Get-Service -Name "AAMMerger-Worker" -ErrorAction SilentlyContinue
        if ($worker) {
            Restart-Service -Name "AAMMerger-Worker" -Force -ErrorAction SilentlyContinue
            Write-Host "  Worker restarted against pool '$poolName'." -ForegroundColor Green
        }
    }
} elseif ([string]::IsNullOrWhiteSpace($prefectApi)) {
    Write-Warning "No Prefect API configured, so the midnight sync was not scheduled. Manual 'Scan Input Folder' still works. Re-run with -WithPrefectServer (single host) or set PREFECT_API_URL."
}

# A Prefect API was configured but the pool or the midnight deployment is not registered: the
# nightly sync would silently never run. Do not report success.
if (-not [string]::IsNullOrWhiteSpace($prefectApi) -and -not $prefectRegistered) {
    Write-Host "ERROR: the Prefect work pool and the midnight sync were not registered (API: $prefectApi). Fix the Prefect server (its service logs are in data\logs), then re-run this script." -ForegroundColor Red
    exit 1
}

# 8. Smoke Check & Verification
Write-Host "[8/8] Performing Smoke Test & Health Check..." -ForegroundColor Cyan
if (-not $SkipServices) {
    $healthy = $false
    # 0.0.0.0 / :: is not a dialable address; loopback is. A service bound to a
    # specific LAN address must be checked there, not on 127.0.0.1.
    $probeHost = if ($HostAddress -eq "0.0.0.0" -or $HostAddress -eq "::") { "127.0.0.1" } else { $HostAddress }
    $testUrl = "http://${probeHost}:${Port}/health"
    for ($i = 1; $i -le 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri $testUrl -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) {
                Write-Host "  Health Check OK (HTTP 200) from $testUrl" -ForegroundColor Green
                $healthy = $true
                break
            }
        } catch {
            # retry
        }
    }
    if (-not $healthy) {
        Write-Warning "Health check did not respond yet. Check logs in data\logs\nssm-web-stdout.log"
    }
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "       AAM Merger V3 Setup Completed Successfully!          " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
# A handover URL of http://0.0.0.0:8000 is not something a user can open.
# Show the LAN address this machine is actually reachable on.
$displayHost = $HostAddress
if ($HostAddress -eq "0.0.0.0") {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' } |
        Select-Object -First 1).IPAddress
    if ($lanIp) { $displayHost = $lanIp }
}
$hostName = $env:COMPUTERNAME.ToLower()
Write-Host "Web Dashboard (by name): http://${hostName}:${Port}/dashboard"
Write-Host "Web Dashboard (by IP)  : http://${displayHost}:${Port}/dashboard"
Write-Host "If this PC gets a new IP address (DHCP), open it by name, or add the new address to allowed_hosts in config.yaml."
Write-Host "Service Status    : .\scripts\manage_services.ps1 -Action status"
Write-Host "Backup DB         : .\scripts\backup_db.ps1"
Write-Host "Logs Directory    : $ProjectRoot\data\logs\"
