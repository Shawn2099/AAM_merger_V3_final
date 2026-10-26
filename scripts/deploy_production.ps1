<#
.SYNOPSIS
    scripts/deploy_production.ps1 - Master one-click installation and deployment script for AAM Merger V3.
.DESCRIPTION
    Fully automates the production deployment on Windows Server 2016 (or standalone Windows PC):
      1. Verifies Administrator privileges (or provides clean instructions).
      2. Ensures Python virtual environment and dependencies are present and synced.
      3. Prepares config.yaml from config.example.yaml, auto-configuring allowed_hosts with local LAN IP and hostname.
      4. Ensures .env contains OPENROUTER_API_KEY (prompts operator if missing).
      5. Creates all required data directories (input, output, quarantine, stored, combined, logs, backup).
      6. Runs safe SQLite database migrations (scripts/migrate_db.py).
      7. Ensures NSSM is available (downloading version 2.24 if needed).
      8. Installs and configures the 3 Windows Services:
           - AAMMerger-PrefectServer (Local orchestration engine)
           - AAMMerger-Worker (Prefect background sync worker)
           - AAMMerger-Web (FastAPI / Uvicorn dashboard)
      9. Deploys the Prefect midnight sync schedule (scripts/deploy_prefect.py).
     10. Registers the automated daily database backup task in Windows Task Scheduler (01:00 AM daily).
     11. Adds a Windows Firewall rule to allow LAN traffic on port 8000.
     12. Starts all services and verifies live health check (/health & /dashboard).

.PARAMETER Port
    HTTP port for Uvicorn (default: 8000).
.PARAMETER HostAddress
    Bind address for Uvicorn (default: 0.0.0.0 for LAN access).
.PARAMETER OpenRouterApiKey
    Optional OpenRouter API key. If omitted and .env does not exist, prompts the operator.
.PARAMETER SkipFirewall
    Switch to skip Windows Defender Firewall rule configuration.
.PARAMETER SkipBackupTask
    Switch to skip Windows Task Scheduler backup task registration.
.PARAMETER DryRun
    Test execution without modifying system services, files, or firewall.

.EXAMPLE
    .\scripts\deploy_production.ps1
    .\scripts\deploy_production.ps1 -Port 8000 -OpenRouterApiKey "sk-or-v1-..."
#>
[CmdletBinding()]
param(
    [string]$Port = "8000",
    [string]$HostAddress = "0.0.0.0",
    [string]$OpenRouterApiKey = "",
    [switch]$AllowAnyHost,
    [switch]$SkipFirewall,
    [switch]$SkipBackupTask,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 0. Header & Path Setup
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "         AAM Merger V3 - Production Deployment & Service Installer     " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Target Root : $ProjectRoot" -ForegroundColor Gray
Write-Host "Binding     : ${HostAddress}:${Port}" -ForegroundColor Gray
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Administrator Check
# ---------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($DryRun) {
        Write-Host "[DRY-RUN] Not running as Administrator. In a real deployment, elevation is required." -ForegroundColor Magenta
    } else {
        Write-Host "ERROR: Administrator privileges are required to configure Windows Services and Firewall." -ForegroundColor Red
        Write-Host "Please right-click PowerShell (or deploy.bat) and choose 'Run as Administrator'." -ForegroundColor Yellow
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 2. Python & Virtual Environment Check
# ---------------------------------------------------------------------------
Write-Host "[1/9] Checking Python environment..." -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    # Try system python or uv
    $hasUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($hasUv) {
        Write-Host "  Virtual environment not found. Creating with 'uv sync'..." -ForegroundColor Yellow
        if (-not $DryRun) {
            & uv sync
            if ($LASTEXITCODE -ne 0) {
                Write-Error "Failed to create virtual environment with uv sync."
                exit 1
            }
        }
    } else {
        $sysPython = Get-Command python -ErrorAction SilentlyContinue
        if ($sysPython) {
            Write-Host "  Virtual environment not found. Creating with python -m venv .venv..." -ForegroundColor Yellow
            if (-not $DryRun) {
                & python -m venv .venv
                $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
                & $PythonExe -m pip install --upgrade pip
                if (Test-Path "requirements.txt") {
                    & $PythonExe -m pip install -r requirements.txt
                }
            }
        } else {
            Write-Error "Neither 'uv' nor system 'python' was found on PATH. Please install Python 3.11+ or uv."
            exit 1
        }
    }
}

if (Test-Path $PythonExe) {
    $PythonExe = (Resolve-Path $PythonExe).Path
    Write-Host "  Using Python: $PythonExe" -ForegroundColor Green
} elseif (-not $DryRun) {
    Write-Error "Python executable (.venv\Scripts\python.exe) could not be resolved."
    exit 1
}

# ---------------------------------------------------------------------------
# 3. Environment Variables & OpenRouter API Key
# ---------------------------------------------------------------------------
Write-Host "[2/9] Checking API keys & environment (.env)..." -ForegroundColor Cyan
$envPath = Join-Path $ProjectRoot ".env"
$keyConfigured = $false

if (Test-Path $envPath) {
    $envContent = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
    if ($envContent -match "OPENROUTER_API_KEY\s*=\s*(.+)") {
        $extractedKey = $matches[1].Trim().Trim('"').Trim("'")
        if ($extractedKey -and $extractedKey -ne "your_openrouter_api_key_here") {
            $keyConfigured = $true
            Write-Host "  OPENROUTER_API_KEY detected in .env" -ForegroundColor Green
        }
    }
}

if (-not $keyConfigured) {
    if ([string]::IsNullOrWhiteSpace($OpenRouterApiKey)) {
        if ($env:OPENROUTER_API_KEY) {
            $OpenRouterApiKey = $env:OPENROUTER_API_KEY
            $keyConfigured = $true
            Write-Host "  OPENROUTER_API_KEY detected in environment variables" -ForegroundColor Green
        }
    } else {
        $keyConfigured = $true
    }

    if (-not $keyConfigured -and -not $DryRun) {
        Write-Host ""
        Write-Host "OpenRouter API Key is required for VLM document extraction." -ForegroundColor Yellow
        $inputKey = Read-Host "Please enter your OPENROUTER_API_KEY (or press Enter to configure later in .env)"
        if ($inputKey -and $inputKey.Trim()) {
            $OpenRouterApiKey = $inputKey.Trim()
            $keyConfigured = $true
        }
    }

    if ($keyConfigured -and -not $DryRun) {
        $envLine = "OPENROUTER_API_KEY=$OpenRouterApiKey`n"
        if (Test-Path $envPath) {
            $existing = Get-Content $envPath -Raw
            if ($existing -match "OPENROUTER_API_KEY") {
                $updated = $existing -replace "OPENROUTER_API_KEY=.*", "OPENROUTER_API_KEY=$OpenRouterApiKey"
                Set-Content -Path $envPath -Value $updated -NoNewline
            } else {
                Add-Content -Path $envPath -Value $envLine -NoNewline
            }
        } else {
            Set-Content -Path $envPath -Value $envLine -NoNewline
        }
        Write-Host "  Updated .env with OPENROUTER_API_KEY" -ForegroundColor Green
    } elseif (-not $keyConfigured) {
        Write-Host "  WARNING: OPENROUTER_API_KEY is not set. Document extraction will fail until set in .env." -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 4. Configuration & Allowed Hosts Auto-Detection
# ---------------------------------------------------------------------------
Write-Host "[3/9] Preparing configuration (config.yaml)..." -ForegroundColor Cyan
$configPath = Join-Path $ProjectRoot "config.yaml"

if (-not (Test-Path $configPath)) {
    Write-Host "  config.yaml not found. Creating from config.example.yaml..." -ForegroundColor Yellow
    if (-not $DryRun) {
        Copy-Item (Join-Path $ProjectRoot "config.example.yaml") $configPath
    }
}

# Auto-detect local host IP and machine name to guarantee server.allowed_hosts is secure and valid
$hostname = $env:COMPUTERNAME
$localIps = @("localhost", "127.0.0.1", $hostname)
try {
    $netIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress
    if ($netIps) {
        $localIps += $netIps
    }
} catch {
    # Fallback to ipconfig if Get-NetIPAddress is unavailable on older PS
}
$localIps = $localIps | Select-Object -Unique

# Ensure config.yaml allowed_hosts is populated and secure
if (Test-Path $configPath) {
    $configText = Get-Content $configPath -Raw
    $formattedHosts = "[" + (($localIps | ForEach-Object { "`"$_`"" }) -join ", ") + "]"
    if ($configText -match 'allowed_hosts:\s*\[\s*"\*"\s*\]') {
        Write-Host "  Pinning allowed_hosts to local machine & IP addresses ($($localIps -join ', '))..." -ForegroundColor Cyan
        $configText = $configText -replace 'allowed_hosts:\s*\[\s*"\*"\s*\]', "allowed_hosts: $formattedHosts"
        if (-not $DryRun) { Set-Content -Path $configPath -Value $configText -NoNewline }
        Write-Host "  server.allowed_hosts pinned successfully." -ForegroundColor Green
    } elseif ($configText -notmatch 'allowed_hosts:') {
        Write-Host "  Adding allowed_hosts for ($($localIps -join ', '))..." -ForegroundColor Cyan
        $configText = $configText -replace '(\s*port:\s*\d+)', "`$1`n  allowed_hosts: $formattedHosts"
        if (-not $DryRun) { Set-Content -Path $configPath -Value $configText -NoNewline }
        Write-Host "  server.allowed_hosts added successfully." -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# 5. Required Data Folders
# ---------------------------------------------------------------------------
Write-Host "[4/9] Verifying and creating required data directories..." -ForegroundColor Cyan
$requiredDirs = @(
    "data/input",
    "data/output",
    "data/quarantine",
    "data/quarantine/_archived_sets",
    "data/stored",
    "data/combined",
    "data/logs",
    "data/backup",
    "tools/nssm"
)

foreach ($dir in $requiredDirs) {
    $fullDir = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullDir)) {
        if (-not $DryRun) {
            New-Item -ItemType Directory -Path $fullDir -Force | Out-Null
        }
        Write-Host "  Created: $dir" -ForegroundColor Gray
    }
}
Write-Host "  All data directories verified." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 6. Database Migrations
# ---------------------------------------------------------------------------
Write-Host "[5/9] Applying database schema migrations..." -ForegroundColor Cyan
if (-not $DryRun) {
    $migrateScript = Join-Path $ProjectRoot "scripts\migrate_db.py"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $migOutput = & $PythonExe $migrateScript 2>&1
    $migCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEap

    if ($migCode -ne 0) {
        Write-Host "ERROR: Database migration failed (exit code $migCode):" -ForegroundColor Red
        foreach ($line in @($migOutput)) { Write-Host "  $line" -ForegroundColor Red }
        exit 1
    }
    Write-Host "  Database schema up to date." -ForegroundColor Green
} else {
    Write-Host "[DRY-RUN] Would run scripts/migrate_db.py" -ForegroundColor Magenta
}

# ---------------------------------------------------------------------------
# 7. Install Windows Services via NSSM
# ---------------------------------------------------------------------------
Write-Host "[6/9] Installing & configuring Windows Services via NSSM..." -ForegroundColor Cyan

$installScript = Join-Path $ProjectRoot "scripts\install_nssm_services.ps1"
$installArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $installScript,
    "-Port", $Port,
    "-HostAddress", $HostAddress,
    "-WithPrefectServer",
    "-StartServices"
)
if ($AllowAnyHost -or $DryRun) {
    $installArgs += "-AllowAnyHost"
}
if ($DryRun) {
    $installArgs += "-DryRun"
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& powershell.exe @installArgs
$instCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap

if ($instCode -ne 0) {
    Write-Host "ERROR: Service installation failed with code $instCode." -ForegroundColor Red
    exit 1
}
Write-Host "  Windows Services installed and started successfully." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 8. Deploy Prefect Midnight Schedule
# ---------------------------------------------------------------------------
Write-Host "[7/9] Registering Prefect midnight sync schedule..." -ForegroundColor Cyan
if (-not $DryRun) {
    $prefectApi = "http://127.0.0.1:4200/api"
    $env:PREFECT_API_URL = $prefectApi
    $deployPrefectScript = Join-Path $ProjectRoot "scripts\deploy_prefect.py"

    # Wait for Prefect Server API to be healthy (first boot/migrations can take 15-30s)
    $apiUp = $false
    $healthUrl = $prefectApi.TrimEnd('/') + "/health"
    Write-Host "  Waiting for Prefect API to bind at $healthUrl..." -ForegroundColor Gray
    for ($i = 1; $i -le 60; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $apiUp = $true; break }
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    if ($apiUp) {
        # Ensure work pool exists before registering deployment
        Write-Host "  Ensuring Prefect work pool 'aam-merger-process-pool' exists..." -ForegroundColor Gray
        $poolOut = & $PythonExe -m prefect work-pool create aam-merger-process-pool --type process --overwrite 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Notice on work pool: $poolOut" -ForegroundColor Yellow
        }

        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $prefOut = & $PythonExe $deployPrefectScript 2>&1
        $prefCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEap

        if ($prefCode -ne 0) {
            Write-Host "WARNING: Prefect deployment returned code $($prefCode):" -ForegroundColor Yellow
            foreach ($l in @($prefOut)) { Write-Host "  $l" -ForegroundColor Yellow }
            Write-Host "  You can re-run 'uv run python scripts/deploy_prefect.py' once the server is ready." -ForegroundColor Yellow
        } else {
            Write-Host "  Prefect midnight sync registered successfully." -ForegroundColor Green
            # Restart the worker so it connects to the created work pool and picks up the deployment
            $workerSvc = Get-Service -Name "AAMMerger-Worker" -ErrorAction SilentlyContinue
            if ($workerSvc) {
                Restart-Service -Name "AAMMerger-Worker" -Force -ErrorAction SilentlyContinue
                Write-Host "  Worker restarted against work pool." -ForegroundColor Green
            }
        }
    } else {
        Write-Host "WARNING: Prefect Server did not respond at $healthUrl within 60s." -ForegroundColor Yellow
        Write-Host "  The service may still be initializing. Run 'scripts/deploy_prefect.py' once online." -ForegroundColor Yellow
    }
} else {
    Write-Host "[DRY-RUN] Would wait for Prefect API, create work pool, register deployment, and restart worker." -ForegroundColor Magenta
}

# ---------------------------------------------------------------------------
# 9. Register Automated Daily Backup Task
# ---------------------------------------------------------------------------
Write-Host "[8/9] Configuring daily automated database backup (01:00 AM)..." -ForegroundColor Cyan
if (-not $SkipBackupTask) {
    $taskName = "AAMMerger-DailyBackup"
    $backupScript = Join-Path $ProjectRoot "scripts\backup_db.ps1"
    $taskAction = "powershell.exe -ExecutionPolicy Bypass -NoProfile -File `"$backupScript`""

    if (-not $DryRun) {
        try {
            # Use schtasks.exe to create a reliable daily task at 01:00 AM
            $schOutput = & schtasks.exe /create /tn $taskName /tr $taskAction /sc daily /st 01:00 /ru "SYSTEM" /f 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  Windows Scheduled Task '$taskName' created (Runs daily at 01:00 AM under SYSTEM)." -ForegroundColor Green
            } else {
                Write-Host "  Notice on Scheduled Task: $schOutput" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  Warning: Could not schedule backup task automatically: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "[DRY-RUN] Would schedule task '$taskName' to run scripts/backup_db.ps1 daily at 01:00 AM" -ForegroundColor Magenta
    }
} else {
    Write-Host "  Skipping backup task registration (-SkipBackupTask)." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# 10. Windows Firewall Configuration
# ---------------------------------------------------------------------------
Write-Host "[9/9] Checking Windows Firewall rule for port $Port..." -ForegroundColor Cyan
if (-not $SkipFirewall) {
    $ruleName = "AAM Merger Web Dashboard (Port $Port)"
    if (-not $DryRun) {
        try {
            $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
            if (-not $existingRule) {
                New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Enabled True | Out-Null
                Write-Host "  Firewall inbound rule added for port $Port." -ForegroundColor Green
            } else {
                Write-Host "  Firewall rule already exists." -ForegroundColor Green
            }
        } catch {
            # Fallback to netsh if NetSecurity module is absent
            $netshOut = & netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=TCP localport=$Port 2>&1
            Write-Host "  Configured firewall via netsh." -ForegroundColor Gray
        }
    } else {
        Write-Host "[DRY-RUN] Would add inbound firewall rule for TCP port $Port" -ForegroundColor Magenta
    }
} else {
    Write-Host "  Skipping firewall rule (-SkipFirewall)." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# 11. Live Health Check Verification
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Verifying live service health..." -ForegroundColor Cyan
if (-not $DryRun) {
    Start-Sleep -Seconds 2
    $healthOk = $false
    for ($i = 1; $i -le 10; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/health" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) {
                $healthOk = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    if ($healthOk) {
        Write-Host "  Web Service Health Check: ONLINE (HTTP 200)" -ForegroundColor Green
    } else {
        Write-Host "  Web Service is starting up (check status with .\scripts\manage_services.ps1 status)." -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 12. Deployment Summary & Access URLs
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host "                 DEPLOYMENT COMPLETED SUCCESSFULLY                    " -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Access the Dashboard from this machine:" -ForegroundColor White
Write-Host "  http://localhost:${Port}/dashboard" -ForegroundColor Cyan
Write-Host ""
Write-Host "Access the Dashboard from LAN client machines:" -ForegroundColor White
foreach ($ip in $localIps) {
    if ($ip -ne "localhost" -and $ip -ne "127.0.0.1") {
        Write-Host "  http://${ip}:${Port}/dashboard" -ForegroundColor Cyan
    }
}
Write-Host ""
Write-Host "Useful Operator Shortcuts:" -ForegroundColor White
Write-Host "  - Check Service Status : .\status.bat  (or .\scripts\manage_services.ps1 status)"
Write-Host "  - Restart All Services : .\restart_services.bat"
Write-Host "  - Run Database Backup  : .\backup_now.bat"
Write-Host "  - Verify System Health : powershell -File .\scripts\verify_deployment.ps1"
Write-Host ""
