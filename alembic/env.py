from __future__ import annotations

import sys
from pathlib import Path

# ensure src on path, cross-platform
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from logging.config import fileConfig

from alembic import context
from app.core.config import load_config
from app.core.database import get_engine
from app.models import models
from app.models.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    try:
        cfg = load_config()
        return f"sqlite:///{Path(cfg.paths.database_path).as_posix()}"
    except Exception:
        return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = load_config()
    engine = get_engine(cfg)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
