# AAM Merger V3 — Setup & Deployment

Welcome to AAM Merger V3. Depending on whether you are an **Operator/Administrator** or an **Autonomous AI Agent**, choose the appropriate guide below:

---

## 🤖 For AI Agents (OpenCode, Antigravity, Claude Code)
👉 **Read the comprehensive guide:** **[`docs/AGENT_SETUP_GUIDE.md`](./docs/AGENT_SETUP_GUIDE.md)**

It provides an exact, step-by-step autonomous protocol to configure, migrate, deploy, and verify the application from a clean git clone with only a `.env` file provided.

### Quick Autonomous Agent Commands:
```powershell
# 1. Virtual Environment & Dependencies
uv sync

# 2. Database Migration
uv run python scripts/migrate_db.py

# 3. Master Production Deployment (Services + Firewall + Backup Task)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy_production.ps1

# 4. Verification Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_deployment.ps1
```

---

## 👤 For Human Operators & IT Teams
👉 **Read the complete runbook & guide:** **[`docs/DEPLOYMENT_GUIDE.md`](./docs/DEPLOYMENT_GUIDE.md)**

### One-Click Installation:
1. Place `.env` with `OPENROUTER_API_KEY=...` in the project root.
2. Right-click **`deploy.bat`** and select **"Run as Administrator"**.
3. Access the dashboard from your browser: `http://localhost:8000/dashboard` (or `http://<server-ip>:8000/dashboard` on LAN).

### Everyday Desktop Shortcuts:
- **`status.bat`**: Check service and system health.
- **`restart_services.bat`**: Restart services cleanly.
- **`backup_now.bat`**: Run an immediate database backup.
- **`update_app.bat`**: Update code, migrate database, and restart services.
