# Deployment Runbook — Windows Server 2016 / Test Rig

> For a client-office handover, start with
> [`docs/DELIVERY.md`](./docs/DELIVERY.md) — it is the checklist version of
> this page. This file keeps the technical detail.

This guide details the single-host production and test rig setup for **AAM Merger V3** (FastAPI, SQLite WAL, Prefect 3.x, NSSM Windows Services).

---

## 1. Quick Automated Setup (Recommended)

Run PowerShell as **Administrator** from the project directory:

Allow the scripts to run in this PowerShell session and unblock the downloaded files first:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
Get-ChildItem -Recurse *.ps1 | Unblock-File
```

```powershell
.\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup -WithPrefectServer
```

> `-WithPrefectServer` is required on a single host. Without a Prefect API URL
> the worker service cannot start (it exits immediately and NSSM restarts it),
> and the midnight deployment is never registered. The installer now refuses to
> install the worker in that state rather than reporting success.

### What this script automates:
1. **Virtual Environment**: Verifies or creates `.venv` and installs dependencies.
2. **Configuration**: Copies `config.example.yaml` to `config.yaml` (if missing).
3. **Data Directories**: Creates all necessary folders (`data/input`, `output`, `quarantine`, `stored`, `logs`, `backup`).
4. **Database Migration**: Executes `python scripts/migrate_db.py` — initialises
   a fresh SQLite database, or migrates an existing one. (A bare
   `alembic upgrade head` is only correct for an empty database: the live DB
   was created by `create_all`, so it has no `alembic_version` and Alembic
   would try to create existing tables. The script detects the schema, writes a
   pre-migration backup, stamps the proven revision, then upgrades.)
5. **Prefect Deployment**: Registers the midnight sync schedule (`0 0 * * *`).
6. **Firewall**: Adds an inbound Windows Firewall rule for TCP port 8000 (if `-OpenFirewall` is passed).
7. **Daily Backups**: Schedules a daily Windows Scheduled Task at 01:00 AM (if `-ScheduleBackup` is passed).
8. **NSSM Services**: Automatically downloads NSSM (if missing) and installs `AAMMerger-Web` and `AAMMerger-Worker`.
9. **Health Verification**: Performs an automated HTTP smoke check against `http://127.0.0.1:8000/health`.

> **Note on Prefect Server:** If you are running a self-hosted Prefect server on the test rig rather than Prefect Cloud, pass `-WithPrefectServer` to also run the orchestration server as a Windows Service.

> **Work pool + API URL:** on a fresh host the services are installed and
> started first, then the installer waits for the Prefect API, creates the
> `aam-merger-process-pool` work pool (nothing else created it, so the midnight
> deployment used to fail with "Work pool not found"), registers the midnight
> sync, and restarts the worker. Every service receives the same
> `PREFECT_API_URL`; pass `-PrefectApiUrl` to target an external server or
> Prefect Cloud.

---

## 2. Configuration & Secrets

### A. API Key
Create a `.env` file in the project root containing your OpenRouter key:
```env
OPENROUTER_API_KEY=sk-or-v1-...
```

### B. Paths & Settings (`config.yaml`)
Review `config.yaml` to ensure paths match your deployment disk layout (e.g. `C:\AAM\data\...`):
```yaml
paths:
  input_folder: "./data/input"
  output_folder: "./data/output"
  quarantine_folder: "./data/quarantine"
  stored_documents_folder: "./data/stored"
  database_path: "./data/aam_merger.db"
  combined_folder: "./data/combined"
  log_folder: "./data/logs"

server:
  host: "0.0.0.0"
  port: 8000
  # Optional Host-header allow-list (DNS-rebinding hardening). ["*"] accepts
  # any host; list stable names/IPs to close rebinding, e.g. ["aam.local"].
  allowed_hosts: ["*"]
```
> **CRITICAL SQLite Rule:** The SQLite database file must reside on a **local drive** (e.g., `C:` or `D:`). SQLite WAL mode requires POSIX/shared memory locks that are **incompatible with network SMB shares**.

---

## 3. Managing Windows Services (NSSM)

Three scripts are provided in `scripts/`:

### Check Status
```powershell
.\scripts\manage_services.ps1 -Action status
```

### Start / Stop / Restart
```powershell
.\scripts\manage_services.ps1 -Action restart
.\scripts\manage_services.ps1 -Action stop
.\scripts\manage_services.ps1 -Action start
```

### Manual Service Installation / Removal
- Install services:
  ```powershell
  .\scripts\install_nssm_services.ps1 -StartServices
  ```
- Uninstall services:
  ```powershell
  .\scripts\uninstall_nssm_services.ps1
  ```

---

## 4. Service Architecture & Log Files

NSSM wraps the Python processes and redirects all console output. For the pilot, rotation is **disabled** (`AppRotateFiles 0`) so logs are never pruned; the development snapshot task copies them:

| Service Name | Executable & Arguments | Log Files |
|---|---|---|
| `AAMMerger-Web` | `python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000` | `data\logs\nssm-web-stdout.log`<br>`data\logs\nssm-web-stderr.log` |
| `AAMMerger-Worker` | `python.exe -m prefect worker start --pool aam-merger-process-pool` | `data\logs\nssm-worker-stdout.log`<br>`data\logs\nssm-worker-stderr.log` |
| `AAMMerger-PrefectServer` *(optional)* | `python.exe -m prefect server start --host 127.0.0.1 --port 4200` | `data\logs\nssm-prefect-server-stdout.log` |

Application-level logs (from Python's `RotatingFileHandler`) are stored in `data\logs\aam_merger.log` (web process). The worker's own log records are in `nssm-worker-stdout.log` / `nssm-worker-stderr.log`.

---

## 5. Automated Database Backups (SPEC NFR-6)

A dedicated online backup script safely checkpoints the WAL journal and creates an atomic snapshot without stopping the server:

```powershell
.\scripts\backup_db.ps1 -RetentionDays 0 -BackupDir .\data\development\snapshots\db
```

- Backups are timestamped to the microsecond: `data\development\snapshots\db\aam_merger_YYYYMMDD_HHMMSS_ffffff.db` (a counter is appended if the name already exists, so two runs can never overwrite each other).
- Each snapshot is written to a hidden `.partial` file, checked with `PRAGMA integrity_check`, and only then renamed into place. A failed check leaves no backup an operator could mistake for a good one.
- `-RetentionDays 0` keeps every verified backup. Do not use a positive retention on the pilot PC.
- Scheduled automatically when `setup_test_rig.ps1 -ScheduleBackup` is run; `AAMMerger-DevelopmentSnapshot` at 01:30 copies logs/files into the same development folder.

---

## 6. Smoke Testing & Verification

1. **Web Dashboard**: Open `http://<SERVER_IP>:8000/dashboard` in a browser on any machine on the LAN.
2. **Health Check**: `curl http://<SERVER_IP>:8000/health` (returns `{"status":"ok",...}`).
3. **Manual Test Run**: Drop a sample PO/DN/SI PDF into `data/input` and observe automatic classification and grouping in the dashboard.
