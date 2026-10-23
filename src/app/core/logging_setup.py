"""One logging setup for both processes.

The web service configured a RotatingFileHandler in `main.py`, but the nightly
extraction runs in the **worker** process (a Prefect flow, no uvicorn), which
never called it. Extraction errors therefore went only to the NSSM console log,
and the runbook pointed the operator at `aam_merger.log` — which was empty for
the nightly path. Both entry points call `setup_logging` now.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_logging(cfg=None) -> None:
    """Attach the configured rotating file handler, once per process.

    Never raises: a bad log folder must not stop the application from running
    (the app still has console/NSSM logs).
    """
    try:
        if cfg is None:
            from app.core.config import load_config

            cfg = load_config()
        log_dir = Path(cfg.paths.log_folder)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "aam_merger.log"

        max_bytes = int(getattr(cfg.logging, "max_file_size_mb", 10)) * 1024 * 1024
        backup_count = int(getattr(cfg.logging, "backup_count", 5))
        level_str = str(getattr(cfg.logging, "level", "INFO")).upper()
        level = getattr(logging, level_str, logging.INFO)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        # Idempotent across a reload or a second flow run in the same process.
        if any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
            return

        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        handler.setLevel(level)
        root_logger.addHandler(handler)
    except Exception as exc:  # pragma: no cover - console logging still works
        logger.warning("Failed to configure RotatingFileHandler: %s", exc)
