"""FastAPI stub - cross-platform (pathlib, config.yaml, WAL). Health only; real routes in dev."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import load_config

app = FastAPI(title="AAM Merger V3", version="0.1.0")

# Task 9: include sync + po_sets routers (FR-4.3, FR-CONC-1-4)
try:
    from app.api.routes.sync import router as sync_router

    app.include_router(sync_router)
except Exception:
    pass
try:
    from app.api.routes.po_sets import router as po_sets_router

    app.include_router(po_sets_router)
except Exception:
    pass
try:
    from app.api.routes.manual_merger import router as manual_router

    app.include_router(manual_router)
except Exception:
    pass
try:
    from app.api.routes.dashboard import router as dashboard_router

    app.include_router(dashboard_router)
except Exception:
    pass


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
