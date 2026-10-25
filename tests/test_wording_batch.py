"""Batch 1 step 2: operator wording. No server paths, no internal codes, plain
statements about what the sync pause means."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import load_config
from app.core.database import get_engine
from app.models import POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def tmp_cfg(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_wording.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.log_folder = tmp_path / "logs"
    cfg.paths.archive_folder = tmp_path / "archived_sets"
    for p in [
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.log_folder,
        cfg.paths.archive_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine(cfg))
    return cfg


@pytest.fixture()
def client(tmp_cfg, monkeypatch):
    for mod in [
        "app.api.routes.sync",
        "app.api.routes.po_sets",
        "app.api.routes.dashboard",
        "app.flows.sync",
        "app.services.sync_lock",
    ]:
        monkeypatch.setattr(f"{mod}.load_config", lambda path=None: tmp_cfg)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _quarantined_set(cfg) -> int:
    with Session(get_engine(cfg)) as s:
        ps = POSet(po_no_normalized="PO7777", status=POSetStatus.quarantined)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def test_quarantine_page_shows_no_internal_code_or_absolute_path(client, tmp_cfg):
    _quarantined_set(tmp_cfg)
    body = client.get("/quarantine").text
    assert "quarantine_delete" not in body
    assert str(tmp_cfg.paths.archive_folder) not in body


def test_audit_subtitle_describes_what_the_log_holds(client):
    body = client.get("/audit").text
    assert "set removals" in body
    assert "Quarantine Deletions" not in body


def test_audit_header_is_ist(client):
    assert "Time (IST)" in client.get("/audit").text
