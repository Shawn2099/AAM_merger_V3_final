"""Sync route — POST /sync 409 if already running (FR-4.3). Sync def handlers run in threadpool."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from filelock import FileLock, Timeout

from app.core.config import load_config

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sync"])

def get_sync_lock(cfg_path: str | None = None) -> FileLock:
    """Get inter-process file lock in database directory (local host per SPEC §5.1)."""
    cfg = load_config(cfg_path) if cfg_path else load_config()
    db_dir = Path(cfg.paths.database_path).parent.resolve()
    db_dir.mkdir(parents=True, exist_ok=True)
    lock_file = db_dir / ".sync.lock"
    # thread_local=False: the SAME lock instance is acquired in the request
    # thread and released in the _run_sync daemon thread. With filelock's
    # default thread-local state, cross-thread release() is a silent no-op
    # (per-thread lock_file_fd) and the OS lock would only clear via GC.
    return FileLock(lock_file, timeout=0, thread_local=False)


def _is_sync_running(cfg_path: str | None = None) -> bool:
    """Check if sync is actively running across threads or processes (FR-CONC-3)."""
    lock = get_sync_lock(cfg_path)
    if lock.is_locked:
        return True
    try:
        lock.acquire(timeout=0)
        lock.release()
        return False
    except Timeout:
        return True
    except Exception:
        return False


def _run_sync(lock: FileLock, cfg_path: str | None = None) -> None:
    """Background sync job — runs sync_flow then releases file lock.

    Uses daemon thread so TestClient does not block on BackgroundTasks.
    """
    try:
        # Keep lock active for at least 0.5s for immediate second POST 409 window in tests
        time.sleep(0.5)
        from app.flows.sync import sync_flow

        sync_flow(cfg_path=cfg_path)
    except Exception as e:
        logger.exception("Sync flow encountered an unhandled exception: %s", e)
    finally:
        with contextlib.suppress(Exception):
            lock.release(force=True)


@router.post("/sync")
def trigger_sync(cfg_path: str | None = None):
    """Trigger ingestion Sync — rejects with 409 if already running (FR-4.3).

    FastAPI ``def`` handler (sync) runs in threadpool per SPEC §5.3 — never ``async def``.
    HTMX dashboard disables Sync button while running (FR-CONC-3) via GET /sync/status.
    """
    lock = get_sync_lock(cfg_path)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise HTTPException(status_code=409, detail="Sync already running") from None

    # Run in background daemon thread while holding file lock across flow execution
    t = threading.Thread(target=_run_sync, args=(lock,), kwargs={"cfg_path": cfg_path}, daemon=True)
    t.start()
    cfg = load_config(cfg_path) if cfg_path else load_config()
    return {"status": "sync started", "pool": cfg.prefect.work_pool_name}


@router.get("/sync/status")
def sync_status(cfg_path: str | None = None):
    """Pollable status for HTMX disable (FR-CONC-3). 200 with running flag."""
    running = _is_sync_running(cfg_path)
    return {"running": running, "detail": "Sync already running" if running else "idle"}
