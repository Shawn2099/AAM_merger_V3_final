"""FastAPI stub — cross-platform (pathlib, config.yaml, WAL). Health only; real routes in dev."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import load_config

app = FastAPI(title="AAM Merger V3", version="0.1.0")


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
