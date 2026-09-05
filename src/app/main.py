"""FastAPI application entry point for AAM Merger V3.

Cross-platform configuration, WAL SQLite engine, static asset mounting,
RotatingFileHandler logging (SPEC NFR-4), and comprehensive route inclusion.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.manual_merger import router as manual_router
from app.api.routes.po_sets import router as po_sets_router
from app.api.routes.sync import router as sync_router
from app.core.config import load_config

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    try:
        cfg = load_config()
        log_dir = Path(cfg.paths.log_folder)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "aam_merger.log"

        max_bytes = int(getattr(cfg.logging, "max_file_size_mb", 10)) * 1024 * 1024
        backup_count = int(getattr(cfg.logging, "backup_count", 5))
        level_str = getattr(cfg.logging, "level", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)

        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(formatter)
        handler.setLevel(level)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        # Avoid duplicate handlers on reloads
        if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
            root_logger.addHandler(handler)
    except Exception as e:
        logger.warning("Failed to configure RotatingFileHandler: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SPEC §13.1: validate config at startup, outside any try/except —
    # an invalid/missing config must refuse to start, not run degraded.
    load_config()
    _setup_logging()
    yield


app = FastAPI(title="AAM Merger V3", version="0.1.0", lifespan=lifespan)

# Mount static assets
static_dir = Path(__file__).resolve().parent.parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Include routers
app.include_router(sync_router)
app.include_router(po_sets_router)
app.include_router(manual_router)
app.include_router(dashboard_router)


@app.get("/health")
def health() -> JSONResponse:
    cfg = load_config()
    return JSONResponse(
        {
            "status": "ok",
            "version": "0.1.0",
            "config": {
                "input_folder": str(Path(cfg.paths.input_folder).as_posix()),
                "database": str(Path(cfg.paths.database_path).as_posix()),
                "prefect_pool": cfg.prefect.work_pool_name,
            },
        }
    )
