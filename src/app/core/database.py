"""DB — sync engine, WAL, single-writer discipline (SPEC §5.3, §5.1). Cross-platform via pathlib."""

from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from .config import AppConfig

_engine_cache: dict[str, Engine] = {}
_engine_lock = threading.Lock()

#: Database paths whose schema has been created in this process. `create_all`
#: is idempotent but issues schema reads/DDL on every call; the old per-document
#: call was a writer competing with extraction for SQLite's single write lock.
_schema_ready: set[str] = set()
_schema_lock = threading.Lock()


def get_engine(cfg: AppConfig) -> Engine:
    db_path = Path(cfg.paths.database_path).resolve()
    key = db_path.as_posix()
    engine = _engine_cache.get(key)
    if engine is not None:
        return engine

    with _engine_lock:
        engine = _engine_cache.get(key)
        if engine is not None:
            return engine
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite URL — pathlib as_posix for cross-platform, WAL via pragmas
        url = f"sqlite:///{key}"
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30.0})

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.close()

        _engine_cache[key] = engine
        return engine


def ensure_schema(cfg: AppConfig) -> Engine:
    """Return the engine, creating the schema at most once per process/database.

    Safe under threads: the second caller waits for the first `create_all` and
    then reuses the result. A fresh process (or a new database path) still gets
    its schema, so direct service calls from scripts keep working.
    """
    engine = get_engine(cfg)
    key = Path(cfg.paths.database_path).resolve().as_posix()
    if key in _schema_ready:
        return engine
    with _schema_lock:
        if key in _schema_ready:
            return engine
        from app.models.base import Base

        Base.metadata.create_all(engine)
        _schema_ready.add(key)
    return engine


def reset_schema_cache() -> None:
    """Forget the per-process schema cache (tests that delete/recreate a DB)."""
    with _schema_lock:
        _schema_ready.clear()
