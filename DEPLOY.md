# Deployment Runbook — Windows Server 2016 / Test Rig

This guide details the single-host production and test rig setup for **AAM Merger V3** (FastAPI, SQLite WAL, Prefect 3.x, NSSM Windows Services).

---

## 1. Quick Automated Setup (Recommended)

Run PowerShell as **Administrator** from the project directory:

```powershell
.\scripts\setup_test_rig.ps1 -OpenFirewall -ScheduleBackup
```

### What this script automates:
1. **Virtual Environment**: Verifies or creates `.venv` and installs dependencies.
2. **Configuration**: Copies `config.example.yaml` to `config.yaml` (if missing).
3. **Data Directories**: Creates all necessary folders (`data/input`, `output`, `quarantine`, `stored`, `logs`, `backup`).
4. **Database Migration**: Executes `alembic upgrade head` to initialise or migrate SQLite WAL.
5. **Prefect Deployment**: Registers the midnight sync schedule (`0 0 * * *`).
6. **Firewall**: Adds an inbound Windows Firewall rule for TCP port 8000 (if `-OpenFirewall` is passed).
7. **Daily Backups**: Schedules a daily Windows Scheduled Task at 01:00 AM (if `-ScheduleBackup` is passed).
8. **NSSM Services**: Automatically downloads NSSM (if missing) and installs `AAMMerger-Web` and `AAMMerger-Worker`.
9. **Health Verification**: Performs an automated HTTP smoke check against `http://127.0.0.1:8000/health`.

> **Note on Prefect Server:** If you are running a self-hosted Prefect server on the test rig rather than Prefect Cloud, pass `-WithPrefectServer` to also run the orchestration server as a Windows Service.

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

NSSM wraps the Python processes and redirects all console output with automatic 10 MB file rotation:

| Service Name | Executable & Arguments | Log Files |
|---|---|---|
| `AAMMerger-Web` | `python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000` | `data\logs\nssm-web-stdout.log`<br>`data\logs\nssm-web-stderr.log` |
| `AAMMerger-Worker` | `python.exe -m prefect worker start --pool aam-merger-process-pool` | `data\logs\nssm-worker-stdout.log`<br>`data\logs\nssm-worker-stderr.log` |
| `AAMMerger-PrefectServer` *(optional)* | `python.exe -m prefect server start --host 127.0.0.1 --port 4200` | `data\logs\nssm-prefect-server-stdout.log` |

Application-level logs (from Python's `RotatingFileHandler`) are stored in `data\logs\app.log`.

---

## 5. Automated Database Backups (SPEC NFR-6)

A dedicated online backup script safely checkpoints the WAL journal and creates an atomic snapshot without stopping the server:

```powershell
.\scripts\backup_db.ps1 -RetentionDays 30
```

- Backups are timestamped: `data\backup\aam_merger_YYYYMMDD_HHMMSS.db`.
- Backups older than 30 days are automatically pruned.
- Scheduled automatically when `setup_test_rig.ps1 -ScheduleBackup` is run.

---

## 6. Smoke Testing & Verification

1. **Web Dashboard**: Open `http://<SERVER_IP>:8000/dashboard` in a browser on any machine on the LAN.
2. **Health Check**: `curl http://<SERVER_IP>:8000/health` (returns `{"status":"ok",...}`).
3. **Manual Test Run**: Drop a sample PO/DN/SI PDF into `data/input` and observe automatic classification and grouping in the dashboard.
