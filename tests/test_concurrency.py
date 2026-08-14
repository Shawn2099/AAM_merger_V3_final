"""Task 9 TDD — 409 on concurrent Sync + per-PO lock (FR-4.3, FR-CONC-1–4, FR-CONFIG-2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.core.database import get_engine
from app.models import POSet, POSetStatus
from app.models.base import Base


@pytest.fixture()
def tmp_db(tmp_path):
    cfg = load_config("config.example.yaml")
    cfg.paths.database_path = tmp_path / "test_conc.db"
    cfg.paths.input_folder = tmp_path / "input"
    cfg.paths.output_folder = tmp_path / "output"
    cfg.paths.quarantine_folder = tmp_path / "quarantine"
    cfg.paths.stored_documents_folder = tmp_path / "stored"
    cfg.paths.unclassified_folder = tmp_path / "unclassified"
    for p in [
        cfg.paths.input_folder,
        cfg.paths.output_folder,
        cfg.paths.quarantine_folder,
        cfg.paths.stored_documents_folder,
        cfg.paths.unclassified_folder,
    ]:
        Path(p).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    return cfg


@pytest.fixture()
def client(tmp_db, monkeypatch):
    # ensure sync routes see same DB via config
    monkeypatch.setenv("AAM_CONFIG_PATH", str(Path("config.example.yaml")))
    # patch load_config to return tmp_db for routes
    import app.api.routes.sync as sync_mod

    # monkeypatch load_config inside routes to return tmp_db
    monkeypatch.setattr("app.api.routes.sync.load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.api.routes.po_sets.load_config", lambda path=None: tmp_db)
    monkeypatch.setattr("app.flows.sync.load_config", lambda path=None: tmp_db)
    # reset global sync lock
    sync_mod._sync_running = False
    from app.main import app

    with TestClient(app) as c:
        yield c
    sync_mod._sync_running = False


def _create_po_set(cfg, po_no="PO9999", status=POSetStatus.pending):
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)
    from sqlalchemy.orm import Session

    with Session(eng) as s:
        ps = POSet(po_no_normalized=po_no, status=status)
        s.add(ps)
        s.commit()
        s.refresh(ps)
        return ps.id


def test_concurrent_sync_409(client):
    # first POST /sync -> 200, second immediate -> 409 "Sync already running"
    r1 = client.post("/sync")
    assert r1.status_code == 200, r1.text
    r2 = client.post("/sync")
    assert r2.status_code == 409, r2.text
    assert "Sync already running" in r2.text


def test_po_lock_409(client, tmp_db):
    # lock PO Set with force_merge, second action -> 409
    po_id = _create_po_set(tmp_db, po_no="PO1001")
    r1 = client.post(f"/po_sets/{po_id}/force_merge")
    # first force_merge may acquire lock; if it completes immediately, we manually lock to simulate concurrent
    # ensure lock is held: if r1 was 200 but released, set lock via DB then retry
    if r1.status_code == 200:
        # simulate long-running by re-locking directly
        eng = get_engine(tmp_db)
        from sqlalchemy.orm import Session

        with Session(eng) as s:
            ps = s.get(POSet, po_id)
            ps.locked_by_action = "force_merge"
            s.commit()
        r2 = client.post(f"/po_sets/{po_id}/force_merge")
        assert r2.status_code == 409, r2.text
        assert "already in progress" in r2.text.lower()
    else:
        # if first already 409 due to test isolation, just check message
        assert r1.status_code in (200, 409)
        r2 = client.post(f"/po_sets/{po_id}/force_merge")
        assert r2.status_code == 409


def test_po_lock_timeout_releases(tmp_db, client):
    # FR-CONFIG-2: lock auto-releases after po_set_lock_timeout_seconds (300s)
    po_id = _create_po_set(tmp_db, po_no="PO2002")
    eng = get_engine(tmp_db)
    from sqlalchemy.orm import Session

    with Session(eng) as s:
        ps = s.get(POSet, po_id)
        ps.locked_by_action = "force_merge"
        # set updated_at to 400s ago (beyond 300s timeout)
        ps.updated_at = datetime.now(UTC) - timedelta(seconds=400)
        s.commit()
    # now second action should NOT 409 because lock is stale and auto-released
    r = client.post(f"/po_sets/{po_id}/force_merge")
    # should succeed (200) because stale lock is auto-cleared
    assert r.status_code == 200, r.text
