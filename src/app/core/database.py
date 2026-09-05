"""DB — sync engine, WAL, single-writer discipline (SPEC §5.3, §5.1). Cross-platform via pathlib."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from .config import AppConfig

_engine_cache: dict[str, Engine] = {}


def get_engine(cfg: AppConfig) -> Engine:
    db_path = Path(cfg.paths.database_path).resolve()
    key = db_path.as_posix()
    if key in _engine_cache:
        return _engine_cache[key]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # SQLite URL — pathlib as_posix for cross-platform, WAL via pragmas
    url = f"sqlite:///{key}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    _engine_cache[key] = engine
    return engine
