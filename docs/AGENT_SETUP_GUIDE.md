# AAM Merger V3 — Autonomous Agent Setup & Deployment Guide

> **Intended Audience:** Autonomous AI Coding & DevOps Agents (e.g. Antigravity, OpenCode, Claude Code).  
> **Mission:** Autonomously configure, migrate, deploy, and verify AAM Merger V3 from a clean git clone.  
> **User Inputs Provided:** Clean repository clone (`main`) + `.env` file containing `OPENROUTER_API_KEY`.

---

## 1. Quick Copy-Paste Prompt for the User

If you are a human handing this repository to an AI agent, copy and paste this exact prompt to the agent:

```text
Please read docs/AGENT_SETUP_GUIDE.md and execute the setup procedure for AAM Merger V3.
I have already provided the .env file with OPENROUTER_API_KEY in the project root.
Follow the Step-by-Step Autonomous Execution Protocol: ensure config.yaml is ready, data directories exist, database migrations are applied, services or local runners are active, and run scripts/verify_deployment.ps1 to verify the system is fully operational.
Report back with the verification status and the live dashboard URLs.
```

---

## 2. Agent Operational Overview

AAM Merger V3 is an AI-assisted 3-way reconciliation engine (PO ordered vs DN delivered vs SI invoiced) built with:
- **Web Dashboard:** FastAPI / Uvicorn + Jinja2 + HTMX + Alpine.js (listening on port `8000`)
- **Database:** SQLite with WAL mode (`data/aam_merger.db`), accessed via synchronous SQLAlchemy 2.x
- **Orchestration:** Prefect 3.x process pool worker (`aam-merger-process-pool`) + local Prefect server (`http://127.0.0.1:4200/api`)
- **Windows Services:** Managed via NSSM 2.24 (`AAMMerger-Web`, `AAMMerger-Worker`, `AAMMerger-PrefectServer`)

---

## 3. Step-by-Step Autonomous Execution Protocol

Execute the following steps in sequence. Do NOT skip steps.

### Step 1: Verify Prerequisites & `.env`
Confirm that the root `.env` file exists and contains a valid API key:
```powershell
if (-not (Test-Path ".env")) {
    throw "ERROR: .env file is missing in the project root. Please provide OPENROUTER_API_KEY in .env."
}
$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch "OPENROUTER_API_KEY\s*=\s*\S+") {
    throw "ERROR: OPENROUTER_API_KEY is missing or empty in .env."
}
Write-Host "Step 1 OK: .env verified." -ForegroundColor Green
```

### Step 2: Virtual Environment & Dependencies
Ensure Python 3.11+ is present and dependencies are installed in `.venv`:
```powershell
# Using uv (preferred and fastest):
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv sync
} else {
    # Fallback to standard Python venv:
    if (-not (Test-Path ".venv")) {
        python -m venv .venv
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
Write-Host "Step 2 OK: Virtual environment ready." -ForegroundColor Green
```

### Step 3: Configure `config.yaml` & Pin `allowed_hosts`
`config.yaml` must exist and `server.allowed_hosts` must NOT contain `["*"]` (which NSSM installer rejects for security):
```powershell
if (-not (Test-Path "config.yaml")) {
    Copy-Item "config.example.yaml" "config.yaml"
}

# Auto-detect local machine name and IPv4 addresses:
$hostname = $env:COMPUTERNAME
$localIps = @("localhost", "127.0.0.1", $hostname)
try {
    $netIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress
    if ($netIps) { $localIps += $netIps }
} catch {}
$localIps = $localIps | Select-Object -Unique
$formattedHosts = "[" + (($localIps | ForEach-Object { "`"$_`"" }) -join ", ") + "]"

$configText = Get-Content "config.yaml" -Raw
if ($configText -match 'allowed_hosts:\s*\[\s*"\*"\s*\]') {
    $configText = $configText -replace 'allowed_hosts:\s*\[\s*"\*"\s*\]', "allowed_hosts: $formattedHosts"
    Set-Content -Path "config.yaml" -Value $configText -NoNewline
} elseif ($configText -notmatch 'allowed_hosts:') {
    $configText = $configText -replace '(\s*port:\s*\d+)', "`$1`n  allowed_hosts: $formattedHosts"
    Set-Content -Path "config.yaml" -Value $configText -NoNewline
}
Write-Host "Step 3 OK: config.yaml configured with allowed_hosts: $formattedHosts" -ForegroundColor Green
```

### Step 4: Create Required Data Folders
Ensure all runtime directories exist:
```powershell
$dirs = @(
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
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}
Write-Host "Step 4 OK: All data directories created." -ForegroundColor Green
```

### Step 5: Run Database Schema Migration
Always run `scripts/migrate_db.py` (never run raw `alembic upgrade head` directly, as `migrate_db.py` safely inspects and stamps structural revisions):
```powershell
uv run python scripts/migrate_db.py
# (Or: .\.venv\Scripts\python.exe scripts/migrate_db.py)
Write-Host "Step 5 OK: Database migrated to head revision." -ForegroundColor Green
```

### Step 6: Deploy Services (Choose Mode A or Mode B)

#### Mode A: Production Windows Services (Administrator Privileges Available)
If running on a Windows Host or VM with Administrator permissions:
```powershell
# Run the master deployment script:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_production.ps1
```
*What this does:*
1. Downloads NSSM 2.24 if missing.
2. Installs & starts `AAMMerger-PrefectServer`, `AAMMerger-Worker`, and `AAMMerger-Web`.
3. Registers the Prefect midnight sync schedule (`0 0 * * *`).
4. Registers the 01:00 AM daily SQLite backup in Windows Task Scheduler.
5. Adds the Windows Firewall rule for port 8000.

#### Mode B: Local Development / Sandbox Mode (Non-Administrator or Container)
If running in an unprivileged shell where Windows Services cannot be registered:
```powershell
# 1. Start Prefect Server in background (port 4200):
$env:PREFECT_HOME = "$PWD\data\prefect"
Start-Process -FilePath "uv" -ArgumentList "run", "prefect", "server", "start", "--host", "127.0.0.1", "--port", "4200" -WindowStyle Hidden
Start-Sleep -Seconds 5

# 2. Register Prefect Midnight Deployment:
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
uv run python scripts/deploy_prefect.py

# 3. Start Prefect Worker in background:
Start-Process -FilePath "uv" -ArgumentList "run", "prefect", "worker", "start", "--pool", "aam-merger-process-pool" -WindowStyle Hidden

# 4. Start Uvicorn Web Server in background (port 8000):
Start-Process -FilePath "uv" -ArgumentList "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000" -WindowStyle Hidden
Start-Sleep -Seconds 3
```

---

## 4. Verification & Definition of Done

Run the automated verification audit tool:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_deployment.ps1
```

### Success Criteria:
The agent MUST verify the following before claiming completion:
1. `http://127.0.0.1:8000/health` returns HTTP `200` with `{"status": "ok"}`.
2. `http://127.0.0.1:8000/dashboard` returns HTTP `200` (HTML response).
3. `PRAGMA integrity_check` on `data/aam_merger.db` returns `ok`.
4. `PRAGMA journal_mode` returns `wal`.
5. No fatal errors in `data/logs/` or `verify_deployment.ps1`.

---

## 5. Agent Gotchas & Guardrails

| Gotcha | Why It Happens | How the Agent Must Handle It |
|---|---|---|
| `allowed_hosts contains '*'` error | `install_nssm_services.ps1` refuses `["*"]` to protect LAN security. | Auto-populate `server.allowed_hosts` with local machine names/IPs in Step 3. |
| `no such column: documents.po_no_source` | Occurs when using `Base.metadata.create_all()` instead of migration runner. | Always execute `scripts/migrate_db.py`. It inspects table markers and applies revisions safely. |
| `PREFECT_API_URL unset` error | Prefect 3 refuses ephemeral servers when creating deployments. | Ensure `PREFECT_API_URL="http://127.0.0.1:4200/api"` is set in the environment before running `deploy_prefect.py`. |
| Port 8000 already in use | Another process (or an old Uvicorn instance) is running. | Run `Get-Process python*` or check `netstat -ano | findstr :8000`, terminate stale processes, or change `port` in `config.yaml`. |
| Windows Service Permission Error | The agent tried to register NSSM services without elevation. | Fall back to Mode B (Local Process runner) if Administrator rights are not granted. |

---

## 6. Continuous Operations Command Cheat Sheet

The agent can recommend or use these convenient root batch scripts:

- **`status.bat`**: Runs service status and system health check.
- **`restart_services.bat`**: Cleanly restarts all 3 services in proper dependency order.
- **`backup_now.bat`**: Creates an immediate, integrity-checked SQLite backup in `data/backup/`.
- **`update_app.bat`**: Safely pulls git updates, applies migrations, and restarts services.
