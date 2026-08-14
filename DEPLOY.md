# Deploy — WS2016 (single host, NSSM, WAL)

1. Copy `config.example.yaml` → `C:\AAM\config.yaml` (or `config.yaml` next to exe), set `paths.*` to `C:\AAM\*`, `server.host=0.0.0.0`, `port=8000`, open firewall for port.
2. `.env` with `OPENROUTER_API_KEY` next to config (never in config.yaml).
3. `pip install` via `uv sync` (or `pip install -r requirements`) with Python 3.11. Use `uv` to install 3.11 on WS2016 if needed.
4. `alembic upgrade head` (creates SQLite WAL at `C:\AAM\db\aam_merger.sqlite3` via config).
5. Prefect: `prefect work-pool create aam-merger-process-pool --type process` (once, dev + prod each create their own), `prefect worker start --pool aam-merger-process-pool`.
6. NSSM: `nssm install AAMMerger "C:\...\ .venv\Scripts\python.exe" " -m uvicorn app.main:app --host 0.0.0.0 --port 8000"` + second service for Prefect worker, auto-restart.
7. Backup: `backup.folder` in config.yaml → simple folder copy, `interval_hours` customizable (SPEC NFR-6). No network-share DB — WAL requires local shared memory on one host.
