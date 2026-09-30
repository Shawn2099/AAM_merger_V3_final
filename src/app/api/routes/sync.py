"""Sync route — POST /sync 409 if already running (FR-4.3). Sync def handlers run in threadpool."""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException

from app.core.config import load_config
from app.services.sync_lock import acquire_sync_lock, release_sync_lock

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sync"])

# Re-exported for backwards compatibility (dashboard imports these names).
from app.services.sync_lock import _is_sync_running, get_sync_lock  # noqa: E402,F401


def _run_sync(lock, cfg_path: str | None = None) -> None:
    """Background sync job — runs sync_flow then releases file lock.

    Uses daemon thread so TestClient does not block on BackgroundTasks.
    The held lock is passed through so sync_flow does not self-deadlock.
    The lock is held for the entire flow run, so the 409 window is exact —
    no artificial sleep needed.
    """
    try:
        from app.flows.sync import sync_flow

        sync_flow(cfg_path=cfg_path, held_lock=lock)
    except Exception as e:
        logger.exception("Sync flow encountered an unhandled exception: %s", e)
    finally:
        release_sync_lock(lock)


@router.post("/sync")
def trigger_sync():
    """Trigger ingestion Sync — rejects with 409 if already running (FR-4.3).

    FastAPI ``def`` handler (sync) runs in threadpool per SPEC §5.3 — never ``async def``.
    HTMX dashboard disables Sync button while running (FR-CONC-3) via GET /sync/status.

    No config path is accepted from the caller. A request-supplied path let any
    LAN host redirect the whole pipeline — output folder, database, log folder —
    and create directories as the service account. The config is the process's
    own, resolved from AAM_CONFIG_PATH or ./config.yaml.
    """
    lock = acquire_sync_lock()
    if lock is None:
        raise HTTPException(status_code=409, detail="Sync already running")

    # Run in background daemon thread while holding file lock across flow execution
    t = threading.Thread(target=_run_sync, args=(lock,), daemon=True)
    t.start()
    cfg = load_config()
    return {"status": "sync started", "pool": cfg.prefect.work_pool_name}


@router.get("/sync/status")
def sync_status():
    """Pollable status for HTMX disable (FR-CONC-3). 200 with running flag."""
    running = _is_sync_running()
    return {"running": running, "detail": "Sync already running" if running else "idle"}
