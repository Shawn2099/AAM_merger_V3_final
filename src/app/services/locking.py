"""Per-PO Concurrency Locking (SPEC §9 FR-CONC-1, FR-CONC-2, FR-CONFIG-2).

Provides unified lock status inspection, acquisition, and release helpers.
Timeout is evaluated against dedicated `locked_at` timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.models import POSet


def is_locked(ps: POSet, cfg: AppConfig) -> bool:
    """Check if a POSet is currently locked by an active action within lock timeout."""
    if ps.locked_by_action is None:
        return False
    locked_at = ps.locked_at
    if locked_at is None:
        # Fallback to updated_at if locked_at was not populated historically
        locked_at = ps.updated_at
    if locked_at is None:
        return True
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    timeout = cfg.concurrency.po_set_lock_timeout_seconds
    return (now - locked_at).total_seconds() <= timeout


def acquire_lock(ps: POSet, action: str, session: Session, cfg: AppConfig) -> bool:
    """Attempt to acquire lock for action on POSet atomically.

    Returns True if acquired, False if already locked by another active action.
    Auto-clears expired locks.
    """
    now = datetime.now(UTC)
    timeout = cfg.concurrency.po_set_lock_timeout_seconds
    threshold = now - timedelta(seconds=timeout)

    stmt = (
        update(POSet)
        .where(
            POSet.id == ps.id,
            or_(
                POSet.locked_by_action.is_(None),
                POSet.locked_at.is_(None),
                POSet.locked_at <= threshold,
            ),
        )
        .values(locked_by_action=action, locked_at=now)
        .execution_options(synchronize_session=False)
    )
    result = session.execute(stmt)
    session.commit()
    if result.rowcount > 0:
        session.refresh(ps)
        return True
    session.refresh(ps)
    return False


def release_lock(ps: POSet, session: Session, action: str | None = None) -> None:
    """Release active lock held on POSet.

    If action is specified, only releases if held by that action.
    """
    stmt = update(POSet).where(POSet.id == ps.id)
    if action is not None:
        stmt = stmt.where(POSet.locked_by_action == action)
    stmt = stmt.values(locked_by_action=None, locked_at=None).execution_options(
        synchronize_session=False
    )
    session.execute(stmt)
    session.commit()
    session.refresh(ps)
