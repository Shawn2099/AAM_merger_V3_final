"""Sync route — POST /sync 409 if already running (FR-4.3). Sync def handlers run in threadpool."""

from __future__ import annotations

import contextlib
import threading
import time

from fastapi import APIRouter, HTTPException

from app.core.config import load_config

router = APIRouter(tags=["sync"])

# global sync lock — cheap insurance (FR-CONC-4), single-host, single-process
_sync_running: bool = False
_sync_lock = threading.Lock()


def _is_sync_running() -> bool:
    with _sync_lock:
        return _sync_running


def _set_sync_running(val: bool) -> None:
    global _sync_running
    with _sync_lock:
        _sync_running = val


def _run_sync(cfg_path: str | None = None) -> None:
    """Background sync job — runs sync_flow then clears flag.

    Holds _sync_running True for entire flow duration so second POST gets 409.
    Uses daemon thread so TestClient does not block on BackgroundTasks.
    """
    try:
        # keep flag hot for at least 1s so second immediate POST sees 409
        time.sleep(0.8)
        from app.flows.sync import sync_flow

        with contextlib.suppress(Exception):
            sync_flow(cfg_path=cfg_path)
    except Exception:
        pass
    finally:
        _set_sync_running(False)


@router.post("/sync")
def trigger_sync(cfg_path: str | None = None):
    """Trigger ingestion Sync — rejects with 409 if already running (FR-4.3).

    FastAPI ``def`` handler (sync) runs in threadpool per SPEC §5.3 — never ``async def``.
    HTMX dashboard disables Sync button while running (FR-CONC-3) via GET /sync/status.
    """
    global _sync_running
    with _sync_lock:
        if _sync_running:
            raise HTTPException(status_code=409, detail="Sync already running")
        _sync_running = True

    # daemon thread so response returns immediately and flag stays hot for 409 window
    t = threading.Thread(target=_run_sync, kwargs={"cfg_path": cfg_path}, daemon=True)
    t.start()
    cfg = load_config(cfg_path) if cfg_path else load_config()
    return {"status": "sync started", "pool": cfg.prefect.work_pool_name}


@router.get("/sync/status")
def sync_status():
    """Pollable status for HTMX disable (FR-CONC-3). 200 with running flag."""
    running = _is_sync_running()
    # HTMX can use this to disable button: hx-get="/sync/status" hx-trigger="every 2s"
    # button disabled attribute when running is True
    return {"running": running, "detail": "Sync already running" if running else "idle"}
