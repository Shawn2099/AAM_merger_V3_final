"""SQLAlchemy base — SPEC §6, cross-platform WAL single-writer."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
