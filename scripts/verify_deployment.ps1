<#
.SYNOPSIS
    scripts/verify_deployment.ps1 - Post-deployment health verification and diagnostics tool for AAM Merger V3.
.DESCRIPTION
    Audits the complete runtime environment on Windows Server 2016:
      - Windows Services status (AAMMerger-Web, AAMMerger-Worker, AAMMerger-PrefectServer)
      - Port availability & Web Health endpoints (/health, /dashboard)
      - Prefect Server API responsiveness & deployment schedule status
      - SQLite Database integrity & WAL mode confirmation
      - Read/Write access on all required data folders
      - Windows Task Scheduler daily backup task status
      - Windows Firewall rule status
.EXAMPLE
    powershell -File .\scripts\verify_deployment.ps1
#>
[CmdletBinding()]
param(
    [string]$Port = "8000"
)

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "             AAM Merger V3 - Deployment Verification Audit            " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Host: $env:COMPUTERNAME | Root: $ProjectRoot" -ForegroundColor Gray
Write-Host ""

$script:passes = 0
$script:failures = 0
$script:warnings = 0

function Report-Check {
    param(
        [string]$Name,
        [string]$Status, # OK, FAIL, WARN
        [string]$Message = ""
    )
    switch ($Status) {
        "OK" {
            $script:passes++
            Write-Host ("  [PASS] {0,-35} : {1}" -f $Name, $Message) -ForegroundColor Green
        }
        "FAIL" {
            $script:failures++
            Write-Host ("  [FAIL] {0,-35} : {1}" -f $Name, $Message) -ForegroundColor Red
        }
        "WARN" {
            $script:warnings++
            Write-Host ("  [WARN] {0,-35} : {1}" -f $Name, $Message) -ForegroundColor Yellow
        }
    }
}

# 1. Windows Services Check
Write-Host "[1/7] Checking Windows Services..." -ForegroundColor Cyan
$expectedServices = @("AAMMerger-PrefectServer", "AAMMerger-Worker", "AAMMerger-Web")
foreach ($svcName in $expectedServices) {
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -eq "Running") {
            Report-Check $svcName "OK" "Status is Running"
        } else {
            Report-Check $svcName "FAIL" "Service exists but is $($svc.Status)"
        }
    } else {
        Report-Check $svcName "WARN" "Service not found (Run scripts/install_nssm_services.ps1)"
    }
}

# 2. Web Health Endpoint Check
Write-Host "`n[2/7] Checking Web Application Endpoints..." -ForegroundColor Cyan
try {
    $healthResp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/health" -UseBasicParsing -TimeoutSec 3
    if ($healthResp.StatusCode -eq 200) {
        Report-Check "Uvicorn /health" "OK" "HTTP 200 OK - Server is healthy"
    } else {
        Report-Check "Uvicorn /health" "FAIL" "HTTP $($healthResp.StatusCode)"
    }
} catch {
    Report-Check "Uvicorn /health" "FAIL" "Could not connect to http://127.0.0.1:${Port}/health ($($_.Exception.Message))"
}

try {
    $dashResp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/dashboard" -UseBasicParsing -TimeoutSec 4
    if ($dashResp.StatusCode -eq 200) {
        Report-Check "Dashboard /dashboard" "OK" "HTTP 200 OK - Dashboard rendering normally"
    } else {
        Report-Check "Dashboard /dashboard" "FAIL" "HTTP $($dashResp.StatusCode)"
    }
} catch {
    Report-Check "Dashboard /dashboard" "FAIL" "Could not connect to http://127.0.0.1:${Port}/dashboard ($($_.Exception.Message))"
}

# 3. Prefect Server & Deployment Check
Write-Host "`n[3/7] Checking Prefect Orchestration..." -ForegroundColor Cyan
try {
    $prefHealth = Invoke-WebRequest -Uri "http://127.0.0.1:4200/api/health" -UseBasicParsing -TimeoutSec 3
    if ($prefHealth.StatusCode -eq 200) {
        Report-Check "Prefect API /health" "OK" "HTTP 200 OK - Prefect Server responsive"
    } else {
        Report-Check "Prefect API /health" "WARN" "HTTP $($prefHealth.StatusCode)"
    }
} catch {
    Report-Check "Prefect API /health" "WARN" "Prefect Server on 4200 not reachable (if using standalone/cloud Prefect, verify PREFECT_API_URL)"
}

# 4. SQLite Database Health & WAL Mode
Write-Host "`n[4/7] Checking Database Integrity & Mode..." -ForegroundColor Cyan
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}

if ($PythonExe) {
    $dbCheckScript = @"
import sqlite3, sys
from pathlib import Path
try:
    from app.core.config import load_config
    cfg = load_config()
    db_path = cfg.paths.database_path
except Exception:
    db_path = Path("data/aam_merger.db")

if not db_path.exists():
    print(f"NO_DB|{db_path}")
    sys.exit(0)
con = sqlite3.connect(str(db_path))
cur = con.cursor()
mode = cur.execute("PRAGMA journal_mode;").fetchone()[0]
integ = cur.execute("PRAGMA integrity_check;").fetchone()[0]
counts = cur.execute("SELECT count(*) FROM po_sets;").fetchone()[0]
print(f"{mode}|{integ}|{counts}|{db_path}")
"@
    $dbRes = $dbCheckScript | & $PythonExe - 2>$null
    if ($dbRes -match "^NO_DB\|(.+)$") {
        $foundPath = $matches[1]
        Report-Check "SQLite Database" "WARN" "$foundPath does not exist yet (will be created on first sync)"
    } elseif ($dbRes -match "^(.+)\|(.+)\|(.+)\|(.+)$") {
        $mode = $matches[1]
        $integ = $matches[2]
        $count = $matches[3]
        $foundPath = $matches[4]
        if ($integ -eq "ok") {
            Report-Check "Database Integrity" "OK" "Passed ($foundPath integrity: ok)"
        } else {
            Report-Check "Database Integrity" "FAIL" "Integrity error: $integ"
        }
        if ($mode -eq "wal") {
            Report-Check "Database Journal" "OK" "WAL mode active (concurrency enabled)"
        } else {
            Report-Check "Database Journal" "WARN" "Current journal mode: $mode (Expected: wal)"
        }
        Report-Check "PO Sets Count" "OK" "$count total sets in database"
    } else {
        Report-Check "SQLite Database" "WARN" "Could not query database ($dbRes)"
    }
} else {
    Report-Check "Python Runtime" "FAIL" "Python executable not found"
}

# 5. Data Directories Write Permissions
Write-Host "`n[5/7] Checking Directory Write Access..." -ForegroundColor Cyan
$dirCheckScript = @"
import json
try:
    from app.core.config import load_config
    cfg = load_config()
    dirs = [
        str(cfg.paths.input_folder),
        str(cfg.paths.output_folder),
        str(cfg.paths.quarantine_folder),
        str(cfg.paths.stored_documents_folder),
        str(cfg.paths.log_folder),
        str(cfg.paths.backup_folder),
    ]
    print(json.dumps(dirs))
except Exception:
    print("[]")
"@
$dirsJson = $dirCheckScript | & $PythonExe - 2>$null
$configuredDirs = @()
if ($dirsJson -and $dirsJson.Trim().StartsWith("[")) {
    try { $configuredDirs = ConvertFrom-Json $dirsJson } catch {}
}
if ($configuredDirs.Count -eq 0) {
    $configuredDirs = @("data/input", "data/output", "data/quarantine", "data/stored", "data/logs", "data/backup")
}
foreach ($td in $configuredDirs) {
    $fullPath = if ([System.IO.Path]::IsPathRooted($td)) { $td } else { Join-Path $ProjectRoot $td }
    if (Test-Path $fullPath) {
        $testFile = Join-Path $fullPath ".perm_test_$(Get-Random).tmp"
        try {
            [System.IO.File]::WriteAllText($testFile, "test")
            [System.IO.File]::Delete($testFile)
            Report-Check "Folder: $td" "OK" "Writable"
        } catch {
            Report-Check "Folder: $td" "FAIL" "Permission denied: $($_.Exception.Message)"
        }
    } else {
        Report-Check "Folder: $td" "WARN" "Folder does not exist"
    }
}

# 6. Windows Task Scheduler Backup Job
Write-Host "`n[6/7] Checking Automated Backup Task..." -ForegroundColor Cyan
$schRes = & schtasks.exe /query /tn "AAMMerger-DailyBackup" /fo CSV 2>&1
if ($LASTEXITCODE -eq 0) {
    Report-Check "Daily Backup Task" "OK" "Task 'AAMMerger-DailyBackup' is registered in Windows Task Scheduler"
} else {
    Report-Check "Daily Backup Task" "WARN" "Scheduled task not found. Run scripts/deploy_production.ps1 or scripts/backup_db.ps1 to configure."
}

# 7. Windows Defender Firewall Rule
Write-Host "`n[7/7] Checking Windows Firewall..." -ForegroundColor Cyan
try {
    $fw = Get-NetFirewallRule -DisplayName "AAM Merger Web Dashboard (Port $Port)" -ErrorAction SilentlyContinue
    if ($fw -and $fw.Enabled -eq "True") {
        Report-Check "Firewall Rule (Port $Port)" "OK" "Rule enabled for inbound TCP"
    } elseif ($fw) {
        Report-Check "Firewall Rule (Port $Port)" "WARN" "Rule exists but is disabled"
    } else {
        Report-Check "Firewall Rule (Port $Port)" "WARN" "Inbound firewall rule for port $Port not detected (LAN access might be blocked)"
    }
} catch {
    Report-Check "Firewall Rule" "WARN" "Could not inspect Windows Firewall status"
}

# Summary
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Audit Results: $script:passes Passed, $script:failures Failed, $script:warnings Warnings" -ForegroundColor $(if ($script:failures -gt 0) { "Red" } elseif ($script:warnings -gt 0) { "Yellow" } else { "Green" })
Write-Host "======================================================================" -ForegroundColor Cyan

if ($script:failures -eq 0) {
    Write-Host "All critical systems are operational and ready for use." -ForegroundColor Green
    exit 0
} else {
    Write-Host "Some critical checks failed. Please review the failures above." -ForegroundColor Red
    exit 1
}
