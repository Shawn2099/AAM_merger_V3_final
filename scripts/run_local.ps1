<#
.SYNOPSIS
    scripts/run_local.ps1 - Native Windows PowerShell local test runner for AAM Merger V3.
.DESCRIPTION
    Runs database migrations, quality gates (pytest, ruff, ty), and optionally starts
    the local development server for smoke testing.
.PARAMETER NoServer
    Only run database migrations and quality gates; do not start the Uvicorn server.
.PARAMETER Port
    HTTP port to bind Uvicorn (default: 8000).
.PARAMETER HostAddress
    Host interface to bind Uvicorn (default: 127.0.0.1).
.EXAMPLE
    .\scripts\run_local.ps1
    .\scripts\run_local.ps1 -NoServer
#>
[CmdletBinding()]
param(
    [switch]$NoServer,
    [string]$Port = "8000",
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

Write-Host "== AAM Merger V3 - Local Run (PowerShell) ==" -ForegroundColor Cyan
Write-Host "Root: $ProjectRoot | Host: ${HostAddress}:${Port} | NoServer: $NoServer"

# 1) Config + Data Folders
if (-not (Test-Path "config.yaml")) {
    Write-Host "[1/5] config.yaml missing - copying config.example.yaml" -ForegroundColor Yellow
    Copy-Item "config.example.yaml" "config.yaml"
} else {
    Write-Host "[1/5] config.yaml exists" -ForegroundColor Green
}

$folders = @("data/input", "data/output", "data/quarantine", "data/stored", "data/unclassified", "data/logs", "data/samples")
foreach ($f in $folders) {
    if (-not (Test-Path $f)) {
        New-Item -ItemType Directory -Path $f -Force | Out-Null
    }
}
Write-Host "  Data directories verified."

# 2) DB Migration (Using python -m alembic for AppLocker compatibility)
Write-Host "[2/5] Running database migrations (alembic upgrade head)..." -ForegroundColor Cyan
uv run python -m alembic upgrade head
Write-Host "  Database up to date." -ForegroundColor Green

# 3) Gates: pytest + ruff + ty
Write-Host "[3/5] Running quality gates (pytest, ruff, ty)..." -ForegroundColor Cyan
Write-Host "  -> uv run pytest -q"
uv run pytest -q
Write-Host "  -> uv run ruff check ."
uv run ruff check .
Write-Host "  -> uv run ty check src"
uv run ty check src
Write-Host "  All quality gates passed!" -ForegroundColor Green

if ($NoServer) {
    Write-Host "Skipping server (-NoServer specified). Completed successfully." -ForegroundColor Green
    exit 0
}

# 4) Start Uvicorn Server in Background Process
Write-Host "[4/5] Starting Uvicorn at http://${HostAddress}:${Port}..." -ForegroundColor Cyan
$serverProcess = Start-Process -FilePath "uv" -ArgumentList "run", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", $Port -PassThru -WindowStyle Hidden

try {
    # 5) Wait for /health endpoint
    Write-Host "[5/5] Waiting for server health check..."
    $healthy = $false
    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri "http://${HostAddress}:${Port}/health" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) {
                Write-Host "  Health OK after $i s (HTTP $($resp.StatusCode))" -ForegroundColor Green
                $healthy = $true
                break
            }
        } catch {
            # Retry until ready
        }
    }

    if (-not $healthy) {
        Write-Error "Server did not respond healthy within 30 seconds."
        exit 1
    }

    # Endpoint smoke checks
    $checks = @(
        @{ Url = "http://${HostAddress}:${Port}/dashboard"; Label = "Dashboard" },
        @{ Url = "http://${HostAddress}:${Port}/quarantine"; Label = "Quarantine" },
        @{ Url = "http://${HostAddress}:${Port}/audit"; Label = "Audit Log" },
        @{ Url = "http://${HostAddress}:${Port}/manual/merger"; Label = "Manual Merger" }
    )

    foreach ($c in $checks) {
        try {
            $r = Invoke-WebRequest -Uri $c.Url -UseBasicParsing
            Write-Host "  OK $($c.Label) -> HTTP $($r.StatusCode) ($($r.RawContentLength) bytes)" -ForegroundColor Green
        } catch {
            Write-Host "  FAIL $($c.Label) -> $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    Write-Host ""
    Write-Host "== Server running at http://${HostAddress}:${Port}/ ==" -ForegroundColor Cyan
    Write-Host "Press any key to stop server and exit..."
    $null = [System.Console]::ReadKey($true)
} finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Write-Host "Stopping Uvicorn server (PID $($serverProcess.Id))..." -ForegroundColor Yellow
        Stop-Process -Id $serverProcess.Id -Force
    }
}
