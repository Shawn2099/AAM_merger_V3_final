# AAM Merger V3 — Production Deployment & Operations Guide

> **Target Platform:** Windows Server 2016 / Windows 10/11 (64-bit)  
> **Architecture:** Standalone Host with LAN client browser access (`http://<server-ip>:8000`)  
> **Key Services:** FastAPI (Web Dashboard) + Prefect Process Worker + Prefect Server (NSSM Windows Services)

---

## 1. Quick Start: One-Click Installation

For the easiest installation on a production or pilot machine:

1. Copy the `aam_merger` folder to the target host (e.g. `C:\AAM\aam_merger` or `C:\Users\<user>\Desktop\aam_merger`).
2. Right-click **`deploy.bat`** in the root directory and select **"Run as Administrator"**.
3. Follow the on-screen prompts:
   - If `.env` is not present, you will be prompted to paste your `OPENROUTER_API_KEY`.
   - The installer will automatically configure virtual environments, create all data folders, run database migrations, set up NSSM Windows Services, configure the Prefect midnight schedule, register daily database backups in Windows Task Scheduler, and configure the Windows Firewall for LAN access.
4. When finished, the installer displays your direct LAN URLs:
   ```text
   Access the Dashboard from this machine:
     http://localhost:8000/dashboard

   Access the Dashboard from other PCs on the office LAN:
     http://<SERVER-IP>:8000/dashboard
   ```

---

## 2. Desktop & Command Line Shortcuts

The root directory includes pre-configured double-clickable `.bat` scripts for everyday operator use:

| Shortcut | Description | When to use |
|---|---|---|
| **`status.bat`** | Audits the running services, tests HTTP endpoints, checks SQLite WAL integrity, and verifies data folders. | Every morning or whenever diagnosing system health. |
| **`restart_services.bat`** | Restarts all three Windows Services (`Web`, `Worker`, `PrefectServer`) in safe dependency order. | If services need to pick up a configuration change. |
| **`backup_now.bat`** | Executes an immediate, atomic, verified online backup of `aam_merger.db` to `data/backup/`. | Before making major operational changes. |
| **`update_app.bat`** | Pulls git updates (if applicable), syncs dependencies, applies database schema migrations, and cleanly restarts services. | When deploying application bugfixes or feature updates. |

---

## 3. Automated Windows Services (NSSM)

AAM Merger runs as three dedicated Windows Services configured to start automatically on Windows boot:

1. **`AAMMerger-PrefectServer`**: Local Prefect orchestration server listening on `http://127.0.0.1:4200`.
2. **`AAMMerger-Worker`**: Prefect process pool worker (`aam-merger-process-pool`) executing background ingestion, VLM extraction, and reconciliation sweeps.
3. **`AAMMerger-Web`**: Uvicorn ASGI server hosting the FastAPI web application and Operator Dashboard on port `8000`.

### Managing Services Manually via PowerShell:
```powershell
# Check status of all services
.\scripts\manage_services.ps1 -Action status

# Start all services
.\scripts\manage_services.ps1 -Action start

# Stop all services
.\scripts\manage_services.ps1 -Action stop

# Restart all services
.\scripts\manage_services.ps1 -Action restart
```

---

## 4. Automated Daily Database Backups (NFR-6)

- **Schedule:** Runs every night at **01:00 AM** automatically via Windows Task Scheduler task named `AAMMerger-DailyBackup`.
- **Target Folder:** `data/backup/`
- **Mechanism:** Uses SQLite online streaming backup API (`VACUUM INTO` / `sqlite3_backup`). Never locks the live database; runs safely while the web dashboard and worker are active.
- **Verification:** Runs `PRAGMA integrity_check` on the backup copy before finalizing.
- **Retention:** Retains backups for **30 days** by default (auto-prunes older verified snapshots).

To test or run manually:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup_db.ps1
```

---

## 5. Network & Firewall Access (LAN)

To allow colleagues on the same local network (LAN) to view and operate the dashboard:
1. The installer automatically creates an inbound firewall rule:
   - **Name:** `AAM Merger Web Dashboard (Port 8000)`
   - **Protocol:** `TCP`
   - **Local Port:** `8000`
   - **Action:** `Allow`
2. In `config.yaml`, ensure `server.allowed_hosts` includes the server's static IP and computer name (the installer sets this automatically).
3. Client machines simply open Google Chrome or Microsoft Edge and navigate to:
   ```text
   http://<SERVER-IP>:8000/dashboard
   ```

---

## 6. Pre-Flight Diagnostic Check

At any time, you can run a comprehensive health check:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_deployment.ps1
```
This tool checks:
- Service execution states (`Running`)
- HTTP response codes on `/health` and `/dashboard`
- Prefect server availability on port `4200`
- SQLite database integrity & WAL mode (`wal`)
- File write permissions on all `data/*` folders
- Windows Firewall rule status
- Task Scheduler backup task presence

---

## 7. Troubleshooting Cheat Sheet

| Symptom | Cause | Solution |
|---|---|---|
| Dashboard says *"Service Offline"* | `AAMMerger-Web` service is stopped | Double-click `restart_services.bat` or run `.\scripts\manage_services.ps1 -Action restart`. |
| LAN clients cannot connect | Firewall blocking port 8000 or IP changed | Run `verify_deployment.ps1` to check firewall status. Ensure `server.allowed_hosts` in `config.yaml` contains the new IP. |
| Files in `data/input` are not being read | `AAMMerger-Worker` is stopped or API key is invalid | Run `status.bat`. Check `data/logs/nssm-worker-stderr.log` and verify `OPENROUTER_API_KEY` in `.env`. |
| Port 8000 already in use | Another application is bound to 8000 | In `config.yaml` and `deploy_production.ps1`, change port to `8080` or any available port. |
