"""Batch fix A3: a set's quarantine folder marker belongs to the folder it was written into.

Two sets can share a PO number and a content fingerprint. The second set must take a
candidate folder, and the first set's marker must keep naming the first set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import POSet, POSetStatus
from app.models.base import Base
from app.services import quarantine as q


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config.example.yaml")
    c.paths.database_path = tmp_path / "owner.db"
    c.paths.quarantine_folder = tmp_path / "quarantine"
    Path(c.paths.quarantine_folder).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(c))
    return c


def _set(cfg, po: str, *, archived: bool = False) -> POSet:
    """An archived set is excluded from the open-set index, so two can share a PO."""
    from datetime import UTC, datetime

    with Session(get_engine(cfg)) as s:
        ps = POSet(
            po_no_normalized=po,
            status=POSetStatus.quarantined,
            archived_at=datetime.now(UTC) if archived else None,
        )
        s.add(ps)
        s.commit()
        s.refresh(ps)
        s.expunge(ps)
        return ps


def test_the_second_set_gets_its_own_folder_and_the_first_keeps_its_marker(cfg, monkeypatch):
    monkeypatch.setattr(q, "_set_fingerprint", lambda ps: "abc12345")
    a = _set(cfg, "PO9100", archived=True)
    b = _set(cfg, "PO9100")

    folder_a = q._quarantine_set_folder(a, cfg)
    folder_b = q._quarantine_set_folder(b, cfg)

    assert folder_a != folder_b
    assert (folder_a / ".po_set_id").read_text(encoding="utf-8") == str(a.id)
    assert (folder_b / ".po_set_id").read_text(encoding="utf-8") == str(b.id)
    # A second quarantine of B lands in B's folder, not in A's and not a new one.
    assert q._quarantine_set_folder(b, cfg) == folder_b
    assert (folder_a / ".po_set_id").read_text(encoding="utf-8") == str(a.id)
